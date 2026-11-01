import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { BoardHeader, FlagStore, KitErrorLine } from 'fleet/shell';
import { type StatCell } from 'fleet/runner-shell';
import { PanelFilters } from '../../core/panel-filters';
import { injectHostEnvironmentsQuery, injectHostRunnersQuery } from '../../core/host.query';
import { LocalIdentity } from '../../core/identity/app-identity';
import { RunnerLogout } from '../../core/identity/runner-logout';
import { RunnerLiveUpdates } from '../../core/live/runner-live-updates';
import { headerConnectionLabel, headerStatCells } from './app-header.model';
import { AppProfileMenu } from './app-profile-menu';

/**
 * The runner's desktop app header — the shared 48px
 * {@link BoardHeader} chrome, mounted at the app root (`../app.ts`), the same
 * shelf the hub's own header sits on. Mounted there, `AppShell` (`fleet`) enforces header-above-nav-above-content by
 * construction and the header persists across every route, matching the hub.
 *
 * A small container, not a presentational component: it injects the host's
 * runner and environment reads itself for {@link connection} and
 * {@link headerStats} — the header is the host's, on every page, narrowed only by
 * the Runner filter. TanStack dedupes query-key injections, so this component
 * injecting the same host reads the host page and the filters inject costs no
 * extra network request. It also injects
 * {@link RunnerLiveUpdates} directly — the same root-provided
 * singleton `../app.ts` starts — purely to read its `status`/`authFailed` for
 * {@link connection}; it maps them into `BoardHeader`'s input the same way it
 * already maps the dashboard read, never starting or restarting the stream itself.
 *
 * Each runner's pause control lives on the host page, beside the runner it acts on.
 * The identity block ({@link LocalIdentity}) and the profile menu ride in the header's
 * `[header-trailing]` slot — each a self-fetching mini-container of its own, composed
 * here without this component or {@link BoardHeader} knowing anything about identity.
 * The profile menu ({@link AppProfileMenu}) is `@defer`-loaded: its CDK menu stack is
 * interaction-only chrome, and an eager reference from here would keep that stack in the
 * initial chunk (`bzh:frontend-eager-shell-entry`).
 * The `@container board-header (max-width: 699px)` narrow-tier collapse drops the
 * identity at that width so the profile menu — this shell's only appearance switcher in
 * desktop mode — never gets pushed off a phone-width header.
 */
@Component({
  selector: 'app-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AppProfileMenu, BoardHeader, LocalIdentity, KitErrorLine],
  templateUrl: './app-header.html',
  styleUrl: './app-header.css',
})
export class AppHeader {
  private readonly runnersQuery = injectHostRunnersQuery();
  private readonly environmentsQuery = injectHostEnvironmentsQuery();
  private readonly liveUpdates = inject(RunnerLiveUpdates);

  /** The host's flags — the header shows what the operator overrode. */
  protected readonly flags = inject(FlagStore);

  /** The Runner filter, which narrows {@link headerStats} to the runner it names. */
  private readonly filters = inject(PanelFilters);

  /** The shell's one logout owner; the failure of the menu's `Log out` surfaces in this header's own alert. */
  protected readonly logout = inject(RunnerLogout);

  /** The header's connection cell — folds in the live stream's own state,
   * mirroring the hub app root's own `connection` computed
   * (`../../hub/src/app/shell/app.ts`): `reconnecting…` while `SseService` is retrying a
   * drop, `degraded` once the stream's `401` channel has exhausted its bounded
   * re-arm and settled dead rather than merely
   * blipping through one — no dashboard read ever surfaces that on its own, since
   * every other `401` degrades in its own region and the host read itself can
   * still be `ok`. Falls through to the host's runner read's own state — `ok` once it
   * resolves, `offline` on a failed read, `connecting…` for the pending gap before
    * the first read settles — otherwise. */
  protected readonly connection = computed<string>(() =>
    headerConnectionLabel(
      this.liveUpdates.status(),
      this.liveUpdates.authFailed(),
      this.runnersQuery.isPending(),
      this.runnersQuery.isError(),
    ),
  );

  /** The header's live stat cells across the host, or across the runner the Runner
   * filter names — environments in use over the environments, agents in use over every
   * serving runner's ceiling. Withheld (`[]`) until the host's runner and environment reads have resolved at
   * least once, the same stance the hub header's own `spendToday` cell takes
    * rather than show a misleading `Envs 0/0`. */
  protected readonly headerStats = computed<readonly StatCell[]>(() =>
    headerStatCells(
      this.runnersQuery.isPending() || this.environmentsQuery.isPending(),
      this.runnersQuery.data(),
      this.environmentsQuery.data(),
      this.filters.runner(),
    ),
  );
}

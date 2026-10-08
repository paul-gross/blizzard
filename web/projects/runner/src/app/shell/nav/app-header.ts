import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { BoardHeader, KitAvatar, KitMenu, KitMenuItem, KitMenuPanel, type StatCell, ViewportMenu } from 'fleet';
import { injectRunnerDashboardQuery } from '../../core/status.query';
import { LocalIdentity } from '../../core/identity/app-identity';
import { RunnerLogout } from '../../core/identity/runner-logout';
import { LocalPauseControl } from '../../machine/app-pause-control';
import { RunnerLiveUpdates } from '../../core/live/runner-live-updates';
import { headerConnectionLabel, headerStatCells } from './app-header.model';

/**
 * The runner's desktop app header — the shared 48px
 * {@link BoardHeader} chrome, mounted at the app root (`../app.ts`), the same
 * shelf the hub's own header sits on. Mounted there, `AppShell` (`fleet`) enforces header-above-nav-above-content by
 * construction and the header persists across every route, matching the hub.
 *
 * A small container, not a presentational component: it injects
 * {@link injectRunnerDashboardQuery} itself for {@link connection} and
 * {@link headerStats}; `LocalPanel` (`runner/src/app/board/app-panel.ts`)
 * renders no header and reads neither. TanStack dedupes query-key
 * injections, so this component injecting the same dashboard query
 * `LocalPanel` (and several of its rails) also injects costs no extra
 * network request — one poll for the whole app, not two. It also injects
 * {@link RunnerLiveUpdates} directly — the same root-provided
 * singleton `../app.ts` starts — purely to read its `status`/`authFailed` for
 * {@link connection}; it maps them into `BoardHeader`'s input the same way it
 * already maps the dashboard read, never starting or restarting the stream itself.
 *
 * The pause control ({@link LocalPauseControl}), the identity
 * block ({@link LocalIdentity}), and the profile menu ride in the header's
 * `[header-trailing]` slot, exactly as they did inside `LocalPanelLayout` —
 * each a self-fetching mini-container of its own, composed here without this
 * component or {@link BoardHeader} knowing anything about pause state or
 * identity. The `@container board-header (max-width: 699px)` narrow-tier
 * collapse moved with them: it drops the pause control and
 * identity at that width so the profile menu — this shell's only appearance
 * switcher in desktop mode — never gets pushed off a phone-width header.
 */
@Component({
  selector: 'app-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardHeader, CdkMenuTrigger, KitAvatar, KitMenu, KitMenuItem, KitMenuPanel, LocalIdentity, LocalPauseControl, ViewportMenu],
  templateUrl: './app-header.html',
  styleUrl: './app-header.css',
})
export class AppHeader {
  private readonly dashboardQuery = injectRunnerDashboardQuery();
  private readonly liveUpdates = inject(RunnerLiveUpdates);

  /** The shell's one logout owner; the menu item's pending flag and trigger bind to it. */
  protected readonly logout = inject(RunnerLogout);

  /** The header's connection cell — folds in the live stream's own state,
   * mirroring the hub app root's own `connection` computed
   * (`../../hub/src/app/shell/app.ts`): `reconnecting…` while `SseService` is retrying a
   * drop, `degraded` once the stream's `401` channel has exhausted its bounded
   * re-arm and settled dead rather than merely
   * blipping through one — no dashboard read ever surfaces that on its own, since
   * every other `401` degrades in its own region and the dashboard poll itself can
   * still be `ok`. Falls through to the dashboard read's own state — `ok` once it
   * resolves, `offline` on a failed read, `connecting…` for the pending gap before
    * the first read settles — otherwise. */
  protected readonly connection = computed<string>(() =>
    headerConnectionLabel(
      this.liveUpdates.status(),
      this.liveUpdates.authFailed(),
      this.dashboardQuery.isPending(),
      this.dashboardQuery.isError(),
    ),
  );

  /** The header's live stat cells — environments in use/capacity off the
   * environments pool, active agent leases/capacity off the runner section's
   * `capacities`. Withheld (`[]`) until the dashboard read has resolved at
   * least once, the same stance the hub header's own `spendToday` cell takes
    * rather than show a misleading `Envs 0/0`. */
  protected readonly headerStats = computed<readonly StatCell[]>(() =>
    headerStatCells(this.dashboardQuery.isPending(), this.dashboardQuery.data()),
  );
}

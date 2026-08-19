import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { BoardHeader, KitAvatar, KitMenu, KitMenuItem, KitMenuPanel, type StatCell, ViewportMenu } from 'fleet';
import { injectRunnerDashboardQuery, LocalIdentity, LocalPauseControl } from 'local-panel';

/**
 * The runner's desktop app header (issue #325) — the shared 48px
 * {@link BoardHeader} chrome, hoisted out of `LocalPanelLayout` (its old home)
 * up to the app root (`../app.ts`), the same shelf the hub's own header sits
 * on. It used to live *inside* the routed board layout, below the
 * `<router-outlet>` anchor — so it rendered under the tab strip on `/board`
 * and not at all on `/events` or `/board/chunk/:chunkId`. Mounted here,
 * `AppShell` (`fleet`) enforces header-above-nav-above-content by
 * construction and the header persists across every route, matching the hub.
 *
 * A small container, not a presentational component: it injects
 * {@link injectRunnerDashboardQuery} itself for {@link connection} and
 * {@link headerStats} — moved here verbatim from `LocalPanel`
 * (`local-panel/src/lib/local-panel.ts`), which no longer needs either now
 * that it renders no header of its own. TanStack dedupes query-key
 * injections, so this component injecting the same dashboard query
 * `LocalPanel` (and several of its rails) also injects costs no extra
 * network request — one poll for the whole app, not two.
 *
 * The pause control ({@link LocalPauseControl}, issue #133), the identity
 * block ({@link LocalIdentity}), and the profile menu ride in the header's
 * `[header-trailing]` slot, exactly as they did inside `LocalPanelLayout` —
 * each a self-fetching mini-container of its own, composed here without this
 * component or {@link BoardHeader} knowing anything about pause state or
 * identity. The `@container board-header (max-width: 699px)` narrow-tier
 * collapse (issue #163) moved with them: it drops the pause control and
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

  /** The header's connection cell — `ok` once the dashboard read resolves,
   * `offline` on a failed read, `connecting…` for the pending gap before the
   * first read settles. Moved verbatim from `LocalPanel`'s own computed
   * (issue #131), which no longer renders a header to feed it. */
  protected readonly connection = computed<string>(() => {
    if (this.dashboardQuery.isPending()) return 'connecting…';
    if (this.dashboardQuery.isError()) return 'offline';
    return 'ok';
  });

  /** The header's live stat cells — environments in use/capacity off the
   * environments pool, active agent leases/capacity off the runner section's
   * `capacities`. Withheld (`[]`) until the dashboard read has resolved at
   * least once, the same stance the hub header's own `spendToday` cell takes
   * rather than show a misleading `Envs 0/0`. Moved verbatim from
   * `LocalPanel`'s own computed (issue #131). */
  protected readonly headerStats = computed<readonly StatCell[]>(() => {
    if (this.dashboardQuery.isPending()) return [];
    const envs = this.dashboardQuery.data()?.environments?.items ?? [];
    const envsUsed = envs.filter((env) => env.chunk_id != null).length;
    const capacities = this.dashboardQuery.data()?.runner?.capacities;
    return [
      { key: 'envs', label: 'Envs', value: envsUsed, capacity: envs.length },
      { key: 'agents', label: 'Agents', value: capacities?.used ?? 0, capacity: capacities?.max_agents ?? 0 },
    ];
  });
}

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
  template: `
    <fleet-board-header [connection]="connection()" connectionLabel="Runner" tagline="runner · machine panel" [stats]="headerStats()">
      <local-pause-control header-trailing />
      <!-- Display-only: this shell's logout is the menu item below, so it does
           not vanish with the header cell at the narrow tier, and all three of
           the app's profile menus carry the same two items. -->
      <local-identity #identity header-trailing variant="label" />
      <fleet-kit-menu
        header-trailing
        class="menu"
        ariaLabel="Profile menu"
        testid="local-panel-menu"
        [menu]="profileMenu"
      >
        <fleet-kit-avatar trigger />
      </fleet-kit-menu>
    </fleet-board-header>
    <ng-template #profileMenu>
      <fleet-kit-menu-panel testid="local-panel-menu-panel">
        @if (identity.username()) {
          <fleet-kit-menu-item testid="local-panel-logout" (triggered)="identity.logout()">Log out</fleet-kit-menu-item>
        }
        <fleet-kit-menu-item testid="local-panel-appearance" submenu [cdkMenuTriggerFor]="appearanceMenu">
          Appearance
        </fleet-kit-menu-item>
      </fleet-kit-menu-panel>
    </ng-template>
    <ng-template #appearanceMenu>
      <fleet-viewport-menu testid="local-panel-appearance-panel" />
    </ng-template>
  `,
  styles: `
    /* The menu never gives way; the identity does — this is where BoardHeader's
       shrinkable trailing cluster lands, so the username truncates and the only
       route back to mobile keeps its size at every width. */
    .menu {
      display: flex;
      flex: none;
      align-items: center;
      padding: 0 10px;
    }
    local-pause-control {
      flex: none;
    }
    local-identity {
      min-width: 0;
    }
    /* This header's own half of the tiered collapse: BoardHeader can only collapse
       the cells it renders, and left standing these two push the profile menu off
       a phone-width header — where, in desktop mode, that menu is this shell's
       ONLY appearance switcher. The container it declares is named, so the rule
       rides the same breakpoint from out here, on nodes this template owns. */
    @container board-header (max-width: 699px) {
      local-pause-control,
      local-identity {
        display: none;
      }
    }
  `,
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

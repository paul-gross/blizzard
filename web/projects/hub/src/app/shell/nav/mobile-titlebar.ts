import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { KitMenuItem, KitMenuPanel, MobileTitlebar as FleetMobileTitlebar, ViewportMenu } from 'fleet';

import { TenantMenu } from './tenant-menu';
import { injectTenantSwitch } from './tenant-switch';

/**
 * The hub's mobile titlebar (mock screen C's `.appbar`,
 * `../../../docs/designs/mobile/core-flows.html`) — the app root's mobile
 * counterpart to the desktop {@link BoardHeader}/{@link AppNav} pair, picked
 * once in `App` (`../app.ts`) off `ViewportService.mode`, never a per-page
 * `@if` (the same route-table-level fork `matchesMobileViewport` already
 * uses for the board route).
 *
 * A thin wrapper around the shared {@link FleetMobileTitlebar}. This
 * layer only supplies the hub's own live signal (`FleetLiveUpdates`, the same
 * connection state the desktop titlebar's own "Hub" cell derives from) and the
 * shell menu's panel — the appearance switcher (a CDK-menu
 * submenu), declared here rather than projected
 * because a `CdkMenu` cannot see items across an `<ng-content>` boundary.
 * Deliberately **no** board/graphs tabs: mobile navigation lives
 * in the bottom {@link MobileTabBar} instead (mock screen C's `.tabbar`),
 * never here. The fleet component's default `testid`
 * (`'mobile-titlebar'`) is used as-is.
 *
 * The titlebar names the tenant the board is in beside the wordmark. Mobile has
 * no profile menu, so a caller with several memberships finds the tenant
 * switcher in this menu instead, the same {@link TenantMenu} submenu.
 */
@Component({
  selector: 'app-mobile-titlebar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkMenuTrigger, FleetMobileTitlebar, KitMenuItem, KitMenuPanel, TenantMenu, ViewportMenu],
  templateUrl: './mobile-titlebar.html',
})
export class MobileTitlebar {
  /** Whether the SSE spine is currently connected — the same `FleetLiveUpdates`
   * state (`app.ts`'s `streamLive`) the desktop titlebar's own "Hub" cell derives
   * its own connection string from. */
  readonly live = input.required<boolean>();

  /** The memberships and the switch the tenant submenu offers, and the tenant the titlebar names. */
  protected readonly tenants = injectTenantSwitch();
}

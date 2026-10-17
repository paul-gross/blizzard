import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { KitMenuItem, KitMenuPanel, MobileTitlebar as FleetMobileTitlebar, ViewportMenu } from 'fleet';

import { injectLensedProject } from '../../core/lensed-project';
import { ProjectMenu } from './project-menu';
import { TenantMenu } from './tenant-menu';
import { injectTenantSwitch } from './tenant-switch';

/**
 * The hub's mobile titlebar (mock screen C's `.appbar`,
 * `../../../docs/designs/mobile/core-flows.html`) — the app root's mobile
 * counterpart to the desktop {@link BoardHeader}/{@link AppNav} pair.
 *
 * A thin wrapper around the shared {@link FleetMobileTitlebar}. This
 * layer only supplies the hub's own live signal (`FleetLiveUpdates`) and the
 * shell menu's panel — the appearance switcher (a CDK-menu
 * submenu), declared here rather than projected
 * because a `CdkMenu` cannot see items across an `<ng-content>` boundary.
 * No board/graphs tabs here; navigation lives in the
 * bottom {@link MobileTabBar}. The fleet component's default `testid`
 * (`'mobile-titlebar'`) is used as-is.
 *
 * The titlebar names the tenant the board is in beside the wordmark. Mobile has
 * no profile menu, so a caller with several memberships finds the tenant
 * switcher in this menu instead, the same {@link TenantMenu} submenu. The project lens is a submenu here too
 * ({@link ProjectMenu}), the mobile counterpart of the desktop tab row's lens control. With the menu closed the bar
 * still shows the lens: beside the tenant it names the project the lens is on, and nothing more on All.
 */
@Component({
  selector: 'app-mobile-titlebar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkMenuTrigger, FleetMobileTitlebar, KitMenuItem, KitMenuPanel, ProjectMenu, TenantMenu, ViewportMenu],
  templateUrl: './mobile-titlebar.html',
})
export class MobileTitlebar {
  /** Whether the SSE spine is currently connected — the same `FleetLiveUpdates`
   * state (`app.ts`'s `streamLive`) the desktop titlebar's own "Hub" cell derives
   * its own connection string from. */
  readonly live = input.required<boolean>();

  /** The memberships and the switch the tenant submenu offers, and the tenant the titlebar names. */
  protected readonly tenants = injectTenantSwitch();

  /** The project the lens is on, which the bar names beside the tenant — `null` on All. */
  protected readonly lensed = injectLensedProject();
}

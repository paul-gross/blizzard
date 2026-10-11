import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { KitAvatar, KitMenu, KitMenuItem, KitMenuPanel, ViewportMenu } from 'fleet';

import { TenantMenu } from './tenant-menu';
import { injectTenantSwitch } from './tenant-switch';

/**
 * The hub top header's profile menu — the avatar-circle trigger
 * holding `Log out` and the appearance switcher, keeping
 * {@link AppNav}'s tab row to route tabs only. Projected into the shared {@link BoardHeader}'s `[header-trailing]`
 * slot by `App` (`../app.ts`) rather than nested inside {@link AppNav} — the
 * header and the tab row are siblings there, and content projection only
 * reaches an element placed directly inside `<fleet-board-header>`.
 *
 * Built on the kit's CDK-menu primitives: real menu semantics —
 * roving focus, arrow-key traversal, right/left into and out of the Appearance
 * submenu, `Escape` to close. The panel and its items are declared here rather
 * than projected into {@link KitMenu}, because `CdkMenu` finds its items by a
 * content query that does not cross an `<ng-content>` boundary; the Appearance
 * item likewise lives in this panel (only its submenu comes from the shared
 * {@link ViewportMenu}), so the parent menu can reach it by keyboard.
 *
 * A caller with several memberships also finds the tenant switcher here, a
 * {@link TenantMenu} submenu beside Appearance: choosing another tenant reloads
 * the current view in it ({@link injectTenantSwitch}). With one membership there
 * is nothing to switch to, and the item is absent.
 *
 * `logout` re-emits unchanged for the app root's own mutation to own.
 */
@Component({
  selector: 'app-nav-menu',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkMenuTrigger, KitAvatar, KitMenu, KitMenuItem, KitMenuPanel, TenantMenu, ViewportMenu],
  templateUrl: './app-nav-menu.html',
  styleUrl: './app-nav-menu.css',
})
export class AppNavMenu {
  /** Whether the logout is in flight; disables the `Log out` item. */
  readonly logoutPending = input(false);

  /** Fired when `Log out` is triggered; the app root owns the mutation. */
  readonly logout = output<void>();

  /** The memberships and the switch the tenant submenu offers. */
  protected readonly tenants = injectTenantSwitch();
}

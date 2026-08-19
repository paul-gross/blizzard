import { ChangeDetectionStrategy, Component, output } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { KitAvatar, KitMenu, KitMenuItem, KitMenuPanel, ViewportMenu } from 'fleet';

/**
 * The hub top header's profile menu (issue #132) — the avatar-circle trigger
 * burying `Log out` and the appearance switcher, which used to sit in
 * {@link AppNav}'s tab row as a standalone button plus a quiet `⋮` overflow
 * menu. Projected into the shared {@link BoardHeader}'s `[header-trailing]`
 * slot by `App` (`../app.ts`) rather than nested inside {@link AppNav} — the
 * header and the tab row are siblings there, and content projection only
 * reaches an element placed directly inside `<fleet-board-header>`.
 *
 * Built on the kit's CDK-menu primitives (issue #161): real menu semantics —
 * roving focus, arrow-key traversal, right/left into and out of the Appearance
 * submenu, `Escape` to close. The panel and its items are declared here rather
 * than projected into {@link KitMenu}, because `CdkMenu` finds its items by a
 * content query that does not cross an `<ng-content>` boundary; the Appearance
 * item likewise lives in this panel (only its submenu comes from the shared
 * {@link ViewportMenu}), so the parent menu can reach it by keyboard.
 *
 * Presentational only: `logout` re-emits unchanged for the app root's own
 * mutation to own, the same contract {@link AppNav}'s output used to carry.
 */
@Component({
  selector: 'app-nav-menu',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkMenuTrigger, KitAvatar, KitMenu, KitMenuItem, KitMenuPanel, ViewportMenu],
  templateUrl: './app-nav-menu.html',
  styleUrl: './app-nav-menu.css',
})
export class AppNavMenu {
  /** Fired when `Log out` is triggered; the app root owns the mutation. */
  readonly logout = output<void>();
}

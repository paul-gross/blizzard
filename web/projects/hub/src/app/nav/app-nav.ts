import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

import { KitTab, KitTabStrip } from 'fleet';

/**
 * The top nav's tab strip — one `routerLink` per top-level route, with
 * `routerLinkActive` marking the tab for the route currently rendered in the
 * root {@link App}'s `<router-outlet>`. Wears the same {@link KitTabStrip}/
 * {@link KitTab} chrome {@link KitTabs} wears (blizzard#203) — one tab
 * treatment, not two.
 *
 * The `Admin` tab (issue #93) is nav-*gating* only — `showAdmin` (`user:manage`,
 * computed by the app root against the resolved identity) decides whether the tab
 * renders at all; the admin page itself is #94's.
 *
 * The tab row holds **only** the route tabs (issue #132) — `Log out` and the
 * viewport override used to live here behind a standalone button and a quiet
 * {@link KitMenu}; both moved into the top header's profile menu
 * ({@link AppNavMenu}, `./app-nav-menu.ts`), projected through
 * {@link BoardHeader}'s `[header-trailing]` slot in `App` (`../app.ts`)
 * rather than sitting in this row.
 */
@Component({
  selector: 'app-nav',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitTab, KitTabStrip, RouterLink, RouterLinkActive],
  templateUrl: './app-nav.html',
  styleUrl: './app-nav.css',
})
export class AppNav {
  /** Whether the `Admin` tab renders — `user:manage`, gated by the app root. */
  readonly showAdmin = input(false);
}

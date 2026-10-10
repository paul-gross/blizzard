import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

import { KitTab, KitTabStrip, LensLink } from 'fleet/shell';

import { ProjectLensControl } from './project-lens-control';

/**
 * The top nav's tab strip — one `routerLink` per top-level route, with
 * `routerLinkActive` marking the tab for the route currently rendered in the
 * root {@link App}'s `<router-outlet>`. Wears the same {@link KitTabStrip}/
 * {@link KitTab} chrome {@link KitTabs} wears — one tab
 * treatment, not two.
 *
 * The `Admin` tab is nav-*gating* only — `showAdmin` (`user:manage`,
 * computed by the app root against the resolved identity) decides whether the tab
 * renders at all; the admin page itself is #94's.
 *
 * Beside the route tabs sits the project lens control ({@link ProjectLensControl}), which narrows the views to one
 * project. Nothing else rides the tab row — `Log out` and the
 * viewport override live in the top header's profile menu
 * ({@link AppNavMenu}, `./app-nav-menu.ts`), projected through
 * {@link BoardHeader}'s `[header-trailing]` slot in `App` (`../app.ts`).
 */
@Component({
  selector: 'app-nav',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitTab, KitTabStrip, ProjectLensControl, RouterLink, RouterLinkActive, LensLink],
  templateUrl: './app-nav.html',
  styleUrl: './app-nav.css',
})
export class AppNav {
  /** Whether the `Admin` tab renders — `user:manage`, gated by the app root. */
  readonly showAdmin = input(false);

  /** Whether the lens control reads the tenant's projects — once the shell has entered its tenant. */
  readonly lensEnabled = input(false);
}

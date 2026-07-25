import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/**
 * The top nav's tab strip — one `routerLink` per top-level route, with
 * `routerLinkActive` marking the tab for the route currently rendered in the
 * root {@link App}'s `<router-outlet>`.
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
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="nav" data-testid="app-nav">
      <a routerLink="/board" routerLinkActive="active" data-testid="nav-board">Board</a>
      <a routerLink="/graphs" routerLinkActive="active" data-testid="nav-graphs">Graphs</a>
      <a routerLink="/events" routerLinkActive="active" data-testid="nav-events">Events</a>
      @if (showAdmin()) {
        <a routerLink="/admin" routerLinkActive="active" data-testid="nav-admin">Admin</a>
      }
    </nav>
  `,
  styles: `
    :host {
      display: block;
      flex: none;
    }
    .nav {
      display: flex;
      align-items: stretch;
      height: 32px;
      border-bottom: 1px solid var(--bezel);
      background: var(--header-lo);
      font-family: var(--mono);
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
    .nav a {
      display: flex;
      align-items: center;
      padding: 0 16px;
      color: var(--label);
      text-decoration: none;
      border-right: 1px solid var(--line);
    }
    .nav a.active {
      color: var(--amber-hi);
      background: var(--header-hi);
    }
  `,
})
export class AppNav {
  /** Whether the `Admin` tab renders — `user:manage`, gated by the app root. */
  readonly showAdmin = input(false);
}

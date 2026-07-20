import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { KitMenu, ViewportToggle } from 'fleet';

/**
 * The top nav's tab strip — one `routerLink` per top-level route, with
 * `routerLinkActive` marking the tab for the route currently rendered in the
 * root {@link App}'s `<router-outlet>`.
 *
 * Also carries {@link ViewportToggle} — the mobile/desktop shell override
 * (`../docs/designs/mobile/README.md`) — buried behind a quiet
 * {@link KitMenu} flush right (mobile polish feedback item 5) rather than
 * left always visible: the override is chrome an operator reaches for
 * occasionally, not something that should compete with the route tabs.
 */
@Component({
  selector: 'app-nav',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive, KitMenu, ViewportToggle],
  template: `
    <nav class="nav" data-testid="app-nav">
      <a routerLink="/board" routerLinkActive="active" data-testid="nav-board">Board</a>
      <a routerLink="/graphs" routerLinkActive="active" data-testid="nav-graphs">Graphs</a>
      <span class="spacer"></span>
      <fleet-kit-menu class="menu" ariaLabel="Shell options" testid="app-nav-menu">
        <fleet-viewport-toggle />
      </fleet-kit-menu>
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
    .spacer {
      flex: 1;
    }
    /* Quiet chrome, not content: the menu sits flush right, normal-cased so
       it never competes with the nav's own uppercase route tabs. */
    .menu {
      align-items: center;
      padding: 0 10px;
      letter-spacing: normal;
      text-transform: none;
    }
  `,
})
export class AppNav {}

import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/**
 * The top nav's tab strip — one `routerLink` per top-level route, with
 * `routerLinkActive` marking the tab for the route currently rendered in the
 * root {@link App}'s `<router-outlet>`.
 */
@Component({
  selector: 'app-nav',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="nav" data-testid="app-nav">
      <a routerLink="/board" routerLinkActive="active" data-testid="nav-board">Board</a>
      <a routerLink="/graphs" routerLinkActive="active" data-testid="nav-graphs">Graphs</a>
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
export class AppNav {}

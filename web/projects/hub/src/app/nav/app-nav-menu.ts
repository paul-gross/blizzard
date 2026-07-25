import { ChangeDetectionStrategy, Component, output } from '@angular/core';
import { KitAvatar, KitButton, KitMenu, ViewportToggle } from 'fleet';

/**
 * The hub top header's profile menu (issue #132) — the avatar-circle trigger
 * burying `Log out` and the viewport override, which used to sit in
 * {@link AppNav}'s tab row as a standalone button plus a quiet `⋮` overflow
 * menu. Projected into the shared {@link BoardHeader}'s `[header-trailing]`
 * slot by `App` (`../app.ts`) rather than nested inside {@link AppNav} — the
 * header and the tab row are siblings there, and content projection only
 * reaches an element placed directly inside `<fleet-board-header>`.
 *
 * Presentational only: `logout` re-emits unchanged for the app root's own
 * mutation to own, the same contract {@link AppNav}'s output used to carry.
 */
@Component({
  selector: 'app-nav-menu',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAvatar, KitButton, KitMenu, ViewportToggle],
  template: `
    <fleet-kit-menu class="menu" ariaLabel="Profile menu" testid="app-nav-menu">
      <fleet-kit-avatar trigger />
      <div class="menu-body">
        <fleet-kit-button class="logout" testid="nav-logout" (click)="logout.emit()">Log out</fleet-kit-button>
        <fleet-viewport-toggle />
      </div>
    </fleet-kit-menu>
  `,
  styles: `
    :host {
      display: flex;
      align-items: center;
    }
    /* Quiet chrome, not content: normal-cased so it never competes with the
       nav's own uppercase route tabs, same reasoning the old tab-row menu
       carried before it moved here. */
    .menu {
      align-items: center;
      padding: 0 10px;
      letter-spacing: normal;
      text-transform: none;
    }
    .menu-body {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 8px;
    }
  `,
})
export class AppNavMenu {
  /** Fired when `Log out` is clicked; the app root owns the mutation. */
  readonly logout = output<void>();
}

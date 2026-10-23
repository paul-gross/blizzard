import { ChangeDetectionStrategy, Component } from '@angular/core';
import { MobileTabBar as FleetMobileTabBar, type MobileTabItem } from 'fleet/shell';

/**
 * The hub's mobile bottom tab bar (mock screen C's `.tabbar`,
 * `../../../docs/designs/mobile/core-flows.html`) — rendered at the app
 * root, below `<router-outlet>` (`../app.ts`), so it persists across every
 * mobile screen rather than being a per-page fixture.
 *
 * A thin wrapper around the shared {@link FleetMobileTabBar}. This layer builds the hub's
 * {@link MobileTabItem}s: Board, Fleet, Events and Gardening, each routed
 * (`routerLink`/`routerLinkActive`).
 */
@Component({
  selector: 'app-mobile-tab-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetMobileTabBar],
  templateUrl: './mobile-tab-bar.html',
})
export class MobileTabBar {
  protected readonly items: readonly MobileTabItem[] = [
    { testid: 'tab-board', label: 'Board', route: '/board' },
    { testid: 'tab-fleet', label: 'Fleet', route: '/fleet' },
    { testid: 'tab-events', label: 'Events', route: '/events' },
    { testid: 'tab-gardening', label: 'Gardening', route: '/gardening' },
  ];
}

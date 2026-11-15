import { ChangeDetectionStrategy, Component } from '@angular/core';
import { MobileTabBar as FleetMobileTabBar } from 'fleet/shell';
import { MOBILE_TAB_ITEMS } from './navigation.model';

/**
 * The hub's mobile bottom tab bar (mock screen C's `.tabbar`,
 * `../../../docs/designs/mobile/core-flows.html`) — rendered at the app
 * root, below `<router-outlet>` (`../app.ts`), so it persists across every
 * mobile screen rather than being a per-page fixture.
 *
 * A thin wrapper around the shared {@link FleetMobileTabBar}. This layer builds the hub's
 * {@link MobileTabItem}s: Board, Delivery, Fleet, Gardening and Events, each routed
 * (`routerLink`/`routerLinkActive`).
 */
@Component({
  selector: 'app-mobile-tab-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetMobileTabBar],
  templateUrl: './mobile-tab-bar.html',
})
export class MobileTabBar {
  protected readonly items = MOBILE_TAB_ITEMS;
}

import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { MobileTabBar as FleetMobileTabBar, type MobileTabItem } from 'fleet/shell';
import { injectPanelAsks } from '../../core/panel-reads';
import { mobileTabItems } from './mobile-tab-bar.model';

/**
 * The panel's mobile bottom tab bar, a thin wrapper around {@link FleetMobileTabBar}.
 * `Board`, `Events`, and `Host` are always routed. `Asks` has no screen of its own, so
 * it is always inert; it carries the open-ask count across the runners and project the
 * filters leave.
 */
@Component({
  selector: 'app-mobile-tab-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetMobileTabBar],
  templateUrl: './mobile-tab-bar.html',
})
export class MobileTabBar {
  private readonly asks = injectPanelAsks();

  /** The open-ask count for the Asks tab's badge. */
  protected readonly askCount = computed(() => this.asks.data().length);

  protected readonly items = computed<readonly MobileTabItem[]>(() => mobileTabItems(this.askCount()));
}

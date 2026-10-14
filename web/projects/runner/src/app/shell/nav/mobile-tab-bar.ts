import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { MobileTabBar as FleetMobileTabBar, type MobileTabItem } from 'fleet';
import { injectActiveRunner } from '../../core/active-runner';
import { mobileTabItems } from './mobile-tab-bar.model';
import { injectRunnerDashboardQuery } from '../../core/status.query';

/**
 * The panel's mobile bottom tab bar, a thin wrapper around {@link FleetMobileTabBar}.
 * `Host` is always routed; `Board`, `Events`, and `Machine` route to the active runner's
 * pages, and render dimmed and inert on a host page, where no runner is active. `Asks`
 * has no screen of its own, so it is always inert; it carries the active runner's
 * open-ask count badge.
 */
@Component({
  selector: 'app-mobile-tab-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetMobileTabBar],
  templateUrl: './mobile-tab-bar.html',
})
export class MobileTabBar {
  private readonly dashboardQuery = injectRunnerDashboardQuery();
  private readonly activeRunner = injectActiveRunner();

  /** The open-ask count for the Asks tab's badge. */
  protected readonly askCount = computed(() => (this.dashboardQuery.data()?.asks?.items ?? []).length);

  protected readonly items = computed<readonly MobileTabItem[]>(() => mobileTabItems(this.activeRunner(), this.askCount()));
}

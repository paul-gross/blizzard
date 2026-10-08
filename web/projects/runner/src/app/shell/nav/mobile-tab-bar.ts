import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { MobileTabBar as FleetMobileTabBar, type MobileTabItem } from 'fleet';
import { injectRunnerDashboardQuery } from '../../core/status.query';

/**
 * The runner's mobile bottom tab bar, a thin wrapper around {@link FleetMobileTabBar}.
 * `Board` and `Events` are routed; `Asks` and `Transcripts` have no screen of their
 * own, so they render dimmed and inert. `Asks` carries the open-ask count badge.
 */
@Component({
  selector: 'app-mobile-tab-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetMobileTabBar],
  templateUrl: './mobile-tab-bar.html',
})
export class MobileTabBar {
  private readonly dashboardQuery = injectRunnerDashboardQuery();

  /** The open-ask count for the Asks tab's badge. */
  protected readonly askCount = computed(() => (this.dashboardQuery.data()?.asks?.items ?? []).length);

  protected readonly items = computed<readonly MobileTabItem[]>(() => [
    { testid: 'tab-board', label: 'Board', route: '/board', queryParamsHandling: 'preserve' },
    {
      testid: 'tab-asks-runner',
      label: 'Asks',
      inert: true,
      badge: this.askCount(),
      badgeTestid: 'tab-asks-runner-badge',
    },
    { testid: 'tab-transcripts-runner', label: 'Transcripts', inert: true },
    { testid: 'tab-events', label: 'Events', route: '/events', queryParamsHandling: 'preserve' },
  ]);
}

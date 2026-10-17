import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { injectHubQuestionsQuery } from '../../board/questions/questions.query';
import { MobileTabBar as FleetMobileTabBar, type MobileTabItem } from 'fleet/shell';

/**
 * The hub's mobile bottom tab bar (mock screen C's `.tabbar`,
 * `../../../docs/designs/mobile/core-flows.html`) — rendered at the app
 * root, below `<router-outlet>` (`../app.ts`), so it persists across every
 * mobile screen rather than being a per-page fixture.
 *
 * A thin wrapper around the shared {@link FleetMobileTabBar}. This layer builds the hub's
 * {@link MobileTabItem}s: Board, Events, Gardening
 * and Fleet (`/fleet`) are routed (`routerLink`/`routerLinkActive`); Asks renders
 * dimmed and inert, carrying an open-count badge off {@link injectHubQuestionsQuery}.
 */
@Component({
  selector: 'app-mobile-tab-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetMobileTabBar],
  templateUrl: './mobile-tab-bar.html',
})
export class MobileTabBar {
  private readonly questionsQuery = injectHubQuestionsQuery();

  /** The open-ask count for the Asks tab's badge — the same read the glance
   * board's "Needs you" bucket folds an open ask in from. */
  protected readonly askCount = computed(() => (this.questionsQuery.data() ?? []).length);

  protected readonly items = computed<readonly MobileTabItem[]>(() => [
    { testid: 'tab-board', label: 'Board', route: '/board' },
    {
      testid: 'tab-asks',
      label: 'Asks',
      inert: true,
      badge: this.askCount(),
      badgeTestid: 'tab-asks-badge',
    },
    { testid: 'tab-fleet', label: 'Fleet', route: '/fleet' },
    { testid: 'tab-events', label: 'Events', route: '/events' },
    { testid: 'tab-gardening', label: 'Gardening', route: '/gardening' },
  ]);
}

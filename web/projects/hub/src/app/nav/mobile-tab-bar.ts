import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { injectHubQuestionsQuery, MobileTabBar as FleetMobileTabBar, type MobileTabItem } from 'fleet';

/**
 * The hub's mobile bottom tab bar (mock screen C's `.tabbar`,
 * `../../../docs/designs/mobile/core-flows.html`) — rendered at the app
 * root, below `<router-outlet>` (`../app.ts`), so it persists across every
 * mobile screen rather than being a per-page fixture.
 *
 * A thin wrapper around the shared {@link FleetMobileTabBar} (issue #92) —
 * the runner's `local-panel.ts` mounts the same fleet component with its own
 * item list. This layer builds the hub's {@link MobileTabItem}s: Board and
 * Events (blizzard#125 Phase 4) are wired to a route today
 * (`routerLink`/`routerLinkActive`, same idiom as the desktop {@link AppNav});
 * Asks and Fleet are the mock's other two tabs but have no screen of their own
 * yet (the next mobile-shells chunk's work per the mock's footnote) — they
 * render dimmed and inert so they read as "not yet", not as broken links.
 * Asks still carries a live open-count badge off the same
 * {@link injectHubQuestionsQuery} read the glance board's "Needs you" bucket
 * folds in, so the number an operator sees here always agrees with the
 * board's own count.
 */
@Component({
  selector: 'app-mobile-tab-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetMobileTabBar],
  template: ` <fleet-mobile-tab-bar [items]="items()" /> `,
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
    { testid: 'tab-fleet', label: 'Fleet', inert: true },
    { testid: 'tab-events', label: 'Events', route: '/events' },
  ]);
}

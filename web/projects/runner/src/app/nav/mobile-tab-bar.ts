import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { MobileTabBar as FleetMobileTabBar, type MobileTabItem } from 'fleet';
import { injectRunnerDashboardQuery } from 'local-panel';

/**
 * The runner's mobile bottom tab bar (issue #313) — rendered at the app root,
 * below `<router-outlet>` (`../app.ts`), so it persists across `/board` and
 * `/events` rather than being a per-page fixture, mirroring the hub's own
 * `app/nav/mobile-tab-bar.ts`.
 *
 * A thin wrapper around the shared {@link FleetMobileTabBar}, same as the
 * hub's. `Board` and `Events` are wired to a route (`routerLink`/
 * `routerLinkActive`) now that the runner app has real routing — before this
 * phase this bar's one entry (`Machine`) was `active`-only, this shell's sole
 * always-current screen (`local-panel.ts`'s own former doc comment). `Asks`
 * and `Transcripts` carry no screen of their own yet, so they render dimmed
 * and inert, the same "not yet" treatment the hub gives its own unbuilt tabs.
 * `Asks` still carries the same open-count badge the panel's own container
 * folded in before this move, off the one shared dashboard read every rail
 * on this panel already polls.
 */
@Component({
  selector: 'app-mobile-tab-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetMobileTabBar],
  templateUrl: './mobile-tab-bar.html',
})
export class MobileTabBar {
  private readonly dashboardQuery = injectRunnerDashboardQuery();

  /** The open-ask count for the Asks tab's badge — the same read the panel's
   * own asks rail folds in from. */
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

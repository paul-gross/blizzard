import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { asyncState, injectNowSignal, KitAsyncState, runnerSubscriptionLines, type SubscriptionLine } from 'fleet';

import { PanelFilters } from '../../core/panel-filters';
import { injectPanelDashboards, injectPanelProjects } from '../../core/panel-reads';
import {
  injectHostEnvironmentsQuery,
  injectHostRunnersQuery,
  injectHostSubscriptionsQuery,
  injectHostWorkspacesQuery,
} from '../../core/host.query';
import { type HostPanelPool, hostPoolsFor, runnerFocus } from '../../core/host-pools.model';
import { capacitiesByRunner, hostPanelRunners, type HostPanelRunner, runnersNamed } from './host-panel.model';
import { HostPanelView } from './host-panel-view';

/**
 * The board rail's host panel **container** — this host as the hub's board rail shows one:
 * owns the host's subscriptions, runners, workspaces and environments reads, their async-state
 * triad, and the ticking clock each subscription's reading age derives from; the presentational
 * {@link HostPanelView} owns the template (`bzh:frontend-container-presentational`).
 *
 * Under the Runner filter the runners section lists only the named runner. Subscriptions and
 * environments are the host's, so they stay whole: the environments another runner holds dim.
 */
@Component({
  selector: 'app-host-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HostPanelView, KitAsyncState],
  templateUrl: './host-panel.html',
  styleUrl: './host-panel.css',
})
export class HostPanel {
  private readonly subscriptionsQuery = injectHostSubscriptionsQuery();
  protected readonly runnersQuery = injectHostRunnersQuery();
  private readonly workspacesQuery = injectHostWorkspacesQuery();
  private readonly environmentsQuery = injectHostEnvironmentsQuery();
  private readonly filters = inject(PanelFilters);
  private readonly projects = injectPanelProjects();
  private readonly dashboards = injectPanelDashboards('all');

  /** Ticks once a second so each subscription's reading age advances between polls. */
  private readonly now = injectNowSignal();

  /** The host's runners the Runner filter leaves, matched by their name on the host. */
  private readonly scopedRunners = computed(() => runnersNamed(this.runnersQuery.data() ?? [], this.filters.runner()));

  /** Each subscription's usage windows as a line of blocks — the hub board rail's compact reading. */
  protected readonly subscriptions = computed<readonly SubscriptionLine[]>(() =>
    runnerSubscriptionLines(this.subscriptionsQuery.data() ?? [], this.now()),
  );

  /** Each runner's own slot accounting by runner id — the agents in use per binding. */
  private readonly capacities = computed(() => capacitiesByRunner(this.dashboards.data()));

  protected readonly runners = computed<readonly HostPanelRunner[]>(() =>
    hostPanelRunners(this.scopedRunners(), this.capacities()),
  );

  protected readonly pools = computed<readonly HostPanelPool[]>(() =>
    hostPoolsFor(
      {
        workspaces: this.workspacesQuery.data() ?? [],
        groups: this.environmentsQuery.data() ?? [],
        runners: this.runnersQuery.data() ?? [],
        projects: this.projects(),
      },
      runnerFocus(this.filters.runner(), this.scopedRunners(), (runner) => runner.runner_id),
    ),
  );

  /** The runners read leads the triad — it names every holder and spender — and the panel is
   * never empty: each section says when it has nothing. */
  protected readonly triadState = computed(() => asyncState(this.runnersQuery, false));
}

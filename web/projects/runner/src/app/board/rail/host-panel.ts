import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { asyncState, injectNowSignal, KitAsyncState, runnerSubscriptionLines, type SubscriptionLine } from 'fleet';

import { PanelFilters } from '../../core/panel-filters';
import { injectPanelDashboards, injectPanelProjects } from '../../core/panel-reads';
import { slugForEntry } from '../../core/panel-reads.model';
import {
  injectHostEnvironmentsQuery,
  injectHostRunnersQuery,
  injectHostSubscriptionsQuery,
  injectHostWorkspacesQuery,
  runnerNameResolver,
} from '../../host/host.query';
import { hostPanelPools, hostPanelRunners, type HostPanelPool, type HostPanelRunner } from './host-panel.model';
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
  private readonly scopedRunners = computed(() => {
    const name = this.filters.runner();
    const all = this.runnersQuery.data() ?? [];
    return name === null ? all : all.filter((runner) => runner.name === name);
  });

  /** Each subscription's usage windows as a line of blocks — the hub board rail's compact reading. */
  protected readonly subscriptions = computed<readonly SubscriptionLine[]>(() =>
    runnerSubscriptionLines(this.subscriptionsQuery.data() ?? [], this.now()),
  );

  /** Each runner's own slot accounting by runner id — the agents in use per binding. */
  private readonly capacities = computed(
    () => new Map(this.dashboards.data().map((entry) => [entry.owner.runnerId, entry.dashboard.runner.capacities] as const)),
  );

  protected readonly runners = computed<readonly HostPanelRunner[]>(() =>
    hostPanelRunners(this.scopedRunners(), this.capacities()),
  );

  protected readonly pools = computed<readonly HostPanelPool[]>(() => {
    const projects = this.projects();
    const focus =
      this.filters.runner() === null
        ? null
        : new Set(this.scopedRunners().flatMap((runner) => (runner.runner_id === null ? [] : [runner.runner_id])));
    return hostPanelPools(
      this.workspacesQuery.data() ?? [],
      this.environmentsQuery.data() ?? [],
      runnerNameResolver(this.runnersQuery.data() ?? []),
      (entry) => slugForEntry(projects, entry) ?? entry,
      focus,
    );
  });

  /** The runners read leads the triad — it names every holder and spender — and the panel is
   * never empty: each section says when it has nothing. */
  protected readonly triadState = computed(() => asyncState(this.runnersQuery, false));
}

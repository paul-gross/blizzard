import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  asyncState,
  type BeaconTone,
  compactRef,
  FleetWhen,
  injectNowSignal,
  KitAsyncState,
  KitBadge,
  KitBeacon,
  KitPanel,
  KitSwatch,
  runnerSubscriptionPaces,
  SubscriptionPaceGroup,
  type SubscriptionPace,
} from 'fleet';

import { machineChunkRows } from '../board/app-panel.model';
import { PanelFilters } from '../core/panel-filters';
import { injectPanelDashboards, injectPanelLeases, injectPanelProjects, injectRunnerProjects } from '../core/panel-reads';
import { servesProject, slugForEntry } from '../core/panel-reads.model';
import { hostPanelPools } from '../board/rail/host-panel.model';
import { LocalPauseControl } from '../machine/app-pause-control';
import { hostRegistrationSummary } from './host-registration.model';
import { hostRunnerRows, type HostRunnerRow } from './host-runner-row.model';
import { HostRunnerRowView } from './host-runner-row';
import { type HostEnvGroup, hostEnvGroups } from './host-environments.model';
import {
  injectHostEnvironmentsQuery,
  injectHostRegistrationQuery,
  injectHostRunnersQuery,
  injectHostSubscriptionsQuery,
  injectHostWorkspacesQuery,
  runnerNameResolver,
} from './host.query';

/**
 * The `host` route — this host as the hub's Fleet page details one: a title bar naming the host
 * and its registration at its hub, then its subscriptions' usage, then one
 * {@link HostRunnerRowView} per runner it serves or holds halted, then its environments by
 * workspace, each held one naming its chunk and runner.
 *
 * The Runner filter leaves the runner it names; the Project filter, the runners serving that
 * project — a halted runner, serving none, shows only while it is All. Subscriptions and
 * environments are the host's, so they stay whole: an environment another runner holds dims.
 */
@Component({
  selector: 'app-host-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    FleetWhen,
    HostRunnerRowView,
    KitAsyncState,
    KitBadge,
    KitBeacon,
    KitPanel,
    KitSwatch,
    LocalPauseControl,
    RouterLink,
    SubscriptionPaceGroup,
  ],
  templateUrl: './host-page.html',
  styleUrl: './host-page.css',
})
export class HostPage {
  private readonly filters = inject(PanelFilters);
  private readonly registrationQuery = injectHostRegistrationQuery();
  protected readonly runnersQuery = injectHostRunnersQuery();
  private readonly subscriptionsQuery = injectHostSubscriptionsQuery();
  private readonly workspacesQuery = injectHostWorkspacesQuery();
  private readonly environmentsQuery = injectHostEnvironmentsQuery();
  private readonly dashboards = injectPanelDashboards('all');
  private readonly leases = injectPanelLeases({ scope: 'all', byProject: false });
  private readonly projects = injectPanelProjects();
  private readonly runnerProjects = injectRunnerProjects();

  /** Ticks once a second so each subscription's reading age advances between polls. */
  private readonly now = injectNowSignal();

  protected readonly registration = computed(() => hostRegistrationSummary(this.registrationQuery.data()));

  /** The host's name at its hub, `host` before it registers under one. */
  protected readonly hostName = computed(() => this.registrationQuery.data()?.host_name ?? 'host');

  protected readonly hostId = computed(() => this.registrationQuery.data()?.host_id ?? null);

  protected readonly hostRef = computed(() => {
    const id = this.hostId();
    return id ? compactRef(id) : null;
  });

  /** Green once registered, red when refused or cut off, amber on the way there. */
  protected readonly hostTone = computed<BeaconTone>(() => {
    const reg = this.registration();
    if (reg?.state === 'registered') return 'green';
    return reg?.state === 'refused' || reg?.state === 'unreachable' ? 'red' : 'amber';
  });

  private readonly allRows = computed<readonly HostRunnerRow[]>(() =>
    hostRunnerRows(this.runnersQuery.data() ?? [], {
      dashboards: new Map(this.dashboards.data().map((entry) => [entry.owner.runnerId, entry.dashboard] as const)),
      projects: this.runnerProjects(),
      subscriptions: this.subscriptionsQuery.data() ?? [],
      chunks: machineChunkRows(this.leases.data(), this.projects()),
    }),
  );

  /** The runners the filters leave. */
  protected readonly rows = computed<readonly HostRunnerRow[]>(() => {
    const runner = this.filters.runner();
    const project = this.filters.project();
    const byRunner = this.runnerProjects();
    return this.allRows().filter(
      (row) =>
        (runner === null || row.name === runner) &&
        (row.runnerId === null ? project === null : servesProject(byRunner.get(row.runnerId) ?? [], project)),
    );
  });

  protected readonly triadState = computed(() => asyncState(this.runnersQuery, this.rows().length === 0));

  /** Each subscription's usage windows as pace bars, naming the runners spending it. */
  protected readonly subscriptions = computed<readonly SubscriptionPace[]>(() =>
    runnerSubscriptionPaces(this.subscriptionsQuery.data() ?? [], this.now(), runnerNameResolver(this.runnersQuery.data() ?? [])),
  );

  /** The host's environments by workspace, each held one dimmed when a runner the filters leave out holds it. */
  protected readonly environments = computed<readonly HostEnvGroup[]>(() => {
    const projects = this.projects();
    const runner = this.filters.runner();
    const focus =
      runner === null
        ? null
        : new Set(this.rows().flatMap((row) => (row.runnerId === null ? [] : [row.runnerId])));
    const pools = hostPanelPools(
      this.workspacesQuery.data() ?? [],
      this.environmentsQuery.data() ?? [],
      runnerNameResolver(this.runnersQuery.data() ?? []),
      (entry) => slugForEntry(projects, entry) ?? entry,
      focus,
    );
    return hostEnvGroups(pools, projects);
  });

  /** "9 / 18 in use" across every workspace, `null` while none is reported. */
  protected readonly envsLabel = computed(() => {
    const groups = this.environments();
    if (groups.length === 0) return null;
    const inUse = groups.reduce((sum, group) => sum + group.inUse, 0);
    const total = groups.reduce((sum, group) => sum + (group.capacity ?? group.envs.length), 0);
    return `${inUse} / ${total} in use`;
  });
}

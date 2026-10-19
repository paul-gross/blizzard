import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  asyncState,
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

import { machineChunkRows } from '../core/chunk-rows.model';
import { hostPoolsFor, runnerFocus, runnerNameResolver } from '../core/host-pools.model';
import {
  injectHostEnvironmentsQuery,
  injectHostRegistrationQuery,
  injectHostRunnersQuery,
  injectHostSubscriptionsQuery,
  injectHostWorkspacesQuery,
} from '../core/host.query';
import { PanelFilters } from '../core/panel-filters';
import { injectPanelDashboards, injectPanelLeases, injectPanelProjects, injectRunnerProjects } from '../core/panel-reads';
import { dashboardsByRunner } from '../core/panel-reads.model';
import { LocalPauseControl } from '../runners/app-pause-control';
import { envsInUseLabel, hostRefLabel, hostRowsInFilter, hostTone } from './host-page.model';
import { hostRegistrationSummary } from './host-registration.model';
import { hostRunnerRows, type HostRunnerRow } from './host-runner-row.model';
import { HostRunnerRowView } from './host-runner-row';
import { type HostEnvGroup, hostEnvGroups } from './host-environments.model';

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

  protected readonly hostRef = computed(() => hostRefLabel(this.hostId()));

  /** Green once registered, red when refused or cut off, amber on the way there. */
  protected readonly hostTone = computed(() => hostTone(this.registration()));

  private readonly allRows = computed<readonly HostRunnerRow[]>(() =>
    hostRunnerRows(this.runnersQuery.data() ?? [], {
      dashboards: dashboardsByRunner(this.dashboards.data()),
      projects: this.runnerProjects(),
      subscriptions: this.subscriptionsQuery.data() ?? [],
      chunks: machineChunkRows(this.leases.data(), this.projects()),
    }),
  );

  /** The runners the filters leave. */
  protected readonly rows = computed<readonly HostRunnerRow[]>(() =>
    hostRowsInFilter(this.allRows(), this.filters.runner(), this.filters.project(), this.runnerProjects()),
  );

  protected readonly triadState = computed(() => asyncState(this.runnersQuery, this.rows().length === 0));

  /** Each subscription's usage windows as pace bars, naming the runners spending it. */
  protected readonly subscriptions = computed<readonly SubscriptionPace[]>(() =>
    runnerSubscriptionPaces(this.subscriptionsQuery.data() ?? [], this.now(), runnerNameResolver(this.runnersQuery.data() ?? [])),
  );

  /** The host's environments by workspace, each held one dimmed when a runner the filters leave out holds it. */
  protected readonly environments = computed<readonly HostEnvGroup[]>(() =>
    hostEnvGroups(
      hostPoolsFor(
        {
          workspaces: this.workspacesQuery.data() ?? [],
          groups: this.environmentsQuery.data() ?? [],
          runners: this.runnersQuery.data() ?? [],
          projects: this.projects(),
        },
        runnerFocus(this.filters.runner(), this.rows(), (row) => row.runnerId),
      ),
      this.projects(),
    ),
  );

  /** "9 / 18 in use" across every workspace, `null` while none is reported. */
  protected readonly envsLabel = computed(() => envsInUseLabel(this.environments()));
}

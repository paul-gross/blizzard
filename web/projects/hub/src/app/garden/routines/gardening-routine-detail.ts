import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { FLEET_CLOCK, asyncState, errorMessage, injectPendingMutationVariables, isPendingFor, type GraphSummaryView, type KitAsyncStateValue, type RoutineView } from 'fleet';
import { defaultRoutineWindow } from '../core/routine-window';
import { FleetRoutinePanel, type LastSweptRowVm, type MeasurementReadingVm, type RelatedScopeVm, type RoutinePanelVm, type StrategyStepVm } from './routine-panel';
import {
  lastSweptRows,
  measurementReadings,
  proposalCountsRows,
  relatedScopeRows,
  routineByName,
  routineEffectiveGraph,
  routineOverrideRetired,
  routinePanelState,
  routinePanelVm,
  strategySteps,
} from './gardening-routine-detail.model';
import { FleetRoutineProposalCounts, type ProposalCountsRowVm } from './routine-proposal-counts';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectHubGraphQuery, injectHubGraphsQuery } from '../../graphs/graphs.query';
import { injectHubRoutineProposalCountsQuery, injectHubRoutineScopesQuery, injectHubRoutineSweepsQuery, injectHubRoutineTrendQuery, injectHubRoutinesQuery } from '../core/routines.query';
import { injectRoutineLifecycleMutation, type RoutineLifecycleVars } from './routine-lifecycle.mutations';
import { routineLifecycleMutationKey } from '../../core/mutation-keys';
import { map } from 'rxjs';

import { isRoutineBlocked } from './gardening-effective-graph';
import { GardeningRunDialog } from '../runs/gardening-run-dialog';

/**
 * The selected routine's own detail — the right-hand child of
 * `/gardening/routines` (`gardening-routines-page.ts` owns the list beside it):
 * the record, its read-only strategy, its three health readings, and its
 * garden-proposal counts. Mounted by both of that route's children,
 * so the bare one renders the panel's own "nothing selected" empty state. It ships
 * no New/Edit affordance here.
 *
 * A container: it injects the routine, graph, trend, sweeps, scopes, and
 * proposal-counts queries and forwards a plain view model to the presentational
 * {@link FleetRoutinePanel}, which injects no query of its own. The routine and
 * graph reads are the same cache-keyed queries the list beside it already holds, so
 * resolving the routed routine independently costs no second fetch. The scopes read
 * renders the routine's related scope set, each marked whether it is this routine's
 * own default. The proposal-counts read feeds a separate presentational
 * component, {@link FleetRoutineProposalCounts}, gated on its own `asyncState`
 * independently of {@link panelState} (`bzh:frontend-empty-state-gated`).
 *
 * The reporting window is this pane's alone — nothing in the list is cut to it —
 * so it is computed here, once, at construction. The proposal-counts read shares
 * that same window rather than one of its own.
 */
@Component({
  selector: 'app-gardening-routine-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetRoutinePanel, FleetRoutineProposalCounts, GardeningRunDialog],
  templateUrl: './gardening-routine-detail.html',
  styleUrl: '../core/gardening-detail-host.css',
})
export class GardeningRoutineDetail {
  private readonly route = inject(ActivatedRoute);

  private readonly routinesQuery = injectHubRoutinesQuery();
  private readonly graphsQuery = injectHubGraphsQuery();
  private readonly meQuery = injectMeQuery();
  private readonly routineLifecycleMutation = injectRoutineLifecycleMutation();

  /** Every routine id a Retire/Enable mutation is currently pending for, and its own
   * variables (`bzh:frontend-pending-override`) — `GardeningScopeDetail`'s own
   * `pendingScopeLifecycle` shape. */
  private readonly pendingRoutineLifecycle =
    injectPendingMutationVariables<RoutineLifecycleVars>(routineLifecycleMutationKey);

  /** The panel's fixed reporting window (AC 3, AC 4) — read once from `FLEET_CLOCK` at
   * construction, not re-derived per render; a page reload is what refreshes it. */
  private readonly window = defaultRoutineWindow(inject(FLEET_CLOCK)());

  private readonly routines = computed<readonly RoutineView[]>(() => this.routinesQuery.data() ?? []);
  private readonly graphs = computed<readonly GraphSummaryView[]>(() => this.graphsQuery.data() ?? []);

  /** The `routineName` route param, or `null` on the bare child route. Routines are
   * keyed by `name` (unique per routine), not id. */
  private readonly routineNameParam = toSignal(
    this.route.paramMap.pipe(map((params) => params.get('routineName'))),
    { initialValue: null },
  );

  private readonly selectedRoutine = computed<RoutineView | null>(() =>
    routineByName(this.routineNameParam(), this.routines()),
  );

  private readonly effectiveGraph = computed<GraphSummaryView | null>(() =>
    routineEffectiveGraph(this.selectedRoutine(), this.graphs(), this.graphsQuery.isPending()),
  );

  protected readonly blocked = computed<boolean>(() => {
    const routine = this.selectedRoutine();
    return routine !== null && isRoutineBlocked(this.graphs(), this.graphsQuery.isPending(), routine.graph_name);
  });

  /** Whether the current identity may retire/enable a routine (`graph:edit`, the
   * scope panel's own permission) — `null`/pending resolves to `false`,
   * `GardeningScopeDetail.canEditScopes`'s own shape. */
  protected readonly canEdit = computed(() => hasPermission(this.meQuery.data(), 'graph:edit'));

  /** The selected routine's `retired` flag as it will read once a currently pending
   * Retire/Enable settles (`bzh:frontend-pending-override`) — `GardeningScopeDetail
   * .overrideRetired`'s own shape. */
  private readonly overrideRetired = computed<boolean | null>(() =>
    routineOverrideRetired(this.selectedRoutine(), this.pendingRoutineLifecycle()),
  );

  private readonly graphQuery = injectHubGraphQuery(() => this.effectiveGraph()?.graph_id ?? null);
  private readonly trendQuery = injectHubRoutineTrendQuery(
    () => this.selectedRoutine()?.name ?? null,
    () => this.window.since,
    () => this.window.until,
    () => this.window.introducedBoundary,
    () => this.window.periodDays,
  );
  private readonly sweepsQuery = injectHubRoutineSweepsQuery(
    () => this.selectedRoutine()?.routine_id ?? null,
    () => this.window.since,
    () => this.window.until,
  );
  private readonly scopesQuery = injectHubRoutineScopesQuery(() => this.selectedRoutine()?.routine_id ?? null);
  private readonly proposalCountsQuery = injectHubRoutineProposalCountsQuery(
    () => this.selectedRoutine()?.name ?? null,
    () => this.window.since,
    () => this.window.until,
  );

  private readonly strategy = computed<readonly StrategyStepVm[]>(() => strategySteps(this.graphQuery.data()));

  private readonly measurements = computed<readonly MeasurementReadingVm[]>(() =>
    measurementReadings(this.sweepsQuery.data()),
  );

  private readonly lastSwept = computed<readonly LastSweptRowVm[]>(() => lastSweptRows(this.sweepsQuery.data()));

  /** The selected routine's related scopes, each marked whether it is the routine's
   * own default — `null` until the routine-scopes read resolves. */
  private readonly relatedScopes = computed<readonly RelatedScopeVm[] | null>(() =>
    relatedScopeRows(this.scopesQuery.data(), this.selectedRoutine()),
  );

  /** The proposal-counts table's own rows — mapped off the read's
   * `rows`, already scoped to the one selected routine by the query's own `routine`
   * filter. */
  protected readonly proposalCountsRows = computed<readonly ProposalCountsRowVm[]>(() =>
    proposalCountsRows(this.proposalCountsQuery.data()),
  );

  /** The proposal-counts panel's own async state, independent of {@link panelState}
   * (`bzh:frontend-empty-state-gated`) — its own loading/error/empty resolve on this
   * one read, not gated on the record/graph reads the rest of the page renders
   * around. */
  protected readonly proposalCountsState = computed<KitAsyncStateValue>(() =>
    asyncState(this.proposalCountsQuery, (this.proposalCountsQuery.data()?.rows.length ?? 0) === 0),
  );

  protected readonly panelVm = computed<RoutinePanelVm | null>(() =>
    routinePanelVm(this.selectedRoutine(), {
      blocked: this.blocked(),
      strategy: this.strategy(),
      trend: this.trendQuery.data(),
      measurements: this.measurements(),
      lastSwept: this.lastSwept(),
      windowLabel: this.window.label,
      relatedScopes: this.relatedScopes(),
      overrideRetired: this.overrideRetired(),
    }),
  );

  /** Gates only on what the record and `blocked` need — `routinesQuery` to know
   * there is a routine at all, `graphsQuery` to resolve `effectiveGraph`/`blocked`
   * without ever answering a graph-list failure as a confident "blocked". The record is
   * fully derivable from those two once resolved, so it is never held behind the
   * slower, independent `trendQuery`/`sweepsQuery`/`graphQuery` reads their own
   * sections already render around individually. */
  protected readonly panelState = computed<KitAsyncStateValue>(() =>
    routinePanelState(
      this.routineNameParam(),
      this.selectedRoutine(),
      this.routinesQuery,
      this.graphsQuery.isPending(),
      this.graphsQuery.isError(),
    ),
  );

  /** The routine currently running the dialog against — `null` closes it.
   * Only {@link FleetRoutinePanel}'s own `run` output ever sets it, so it can only
   * ever name the already-selected, already-unblocked routine. */
  protected readonly runningRoutine = signal<RoutineView | null>(null);

  protected run(): void {
    this.runningRoutine.set(this.selectedRoutine());
  }

  protected closeDialog(): void {
    this.runningRoutine.set(null);
  }

  /** Set on a failed retire/enable; cleared at the start of the next attempt —
   * `GardeningScopeDetail.scopeActionError`'s own shape. */
  protected readonly lifecycleActionError = signal<string | null>(null);

  /** Whether a retire/enable is in flight for the selected routine, threaded to
   * {@link FleetRoutinePanel}'s Re-enable/Retire buttons — only one of the two is ever
   * shown for the routine's current lifecycle state. */
  protected readonly lifecyclePending = computed(() =>
    isPendingFor(this.pendingRoutineLifecycle(), (v) => v.routineId === this.selectedRoutine()?.routine_id),
  );

  protected onRetireRoutine(name: string): void {
    const routineId = this.routines().find((r) => r.name === name)?.routine_id;
    if (routineId === undefined) return;
    this.lifecycleActionError.set(null);
    this.routineLifecycleMutation.mutate(
      { routineId, retired: true },
      { onError: (error: unknown) => this.lifecycleActionError.set(errorMessage(error, 'Retire failed.')) },
    );
  }

  protected onEnableRoutine(name: string): void {
    const routineId = this.routines().find((r) => r.name === name)?.routine_id;
    if (routineId === undefined) return;
    this.lifecycleActionError.set(null);
    this.routineLifecycleMutation.mutate(
      { routineId, retired: false },
      { onError: (error: unknown) => this.lifecycleActionError.set(errorMessage(error, 'Enable failed.')) },
    );
  }
}

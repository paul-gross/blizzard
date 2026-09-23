import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import {
  asyncState,
  defaultRoutineWindow,
  errorMessage,
  FleetRoutinePanel,
  FleetRoutineProposalCounts,
  hasPermission,
  injectHubGraphQuery,
  injectHubGraphsQuery,
  injectHubRoutineProposalCountsQuery,
  injectHubRoutineScopesQuery,
  injectHubRoutineSweepsQuery,
  injectHubRoutineTrendQuery,
  injectHubRoutinesQuery,
  injectMeQuery,
  injectPendingMutationVariables,
  injectRoutineLifecycleMutation,
  routineLifecycleMutationKey,
  type GraphSummaryView,
  type KitAsyncStateValue,
  type LastSweptRowVm,
  type MeasurementReadingVm,
  type ProposalCountsRowVm,
  type RelatedScopeVm,
  type RoutineLifecycleVars,
  type RoutinePanelVm,
  type RoutineView,
  type StrategyStepVm,
} from 'fleet';
import { map } from 'rxjs';

import { effectiveGraphByName, isRoutineBlocked } from './gardening-effective-graph';
import { GardeningRunDialog } from './gardening-run-dialog';

/**
 * The selected routine's own detail — the right-hand child of
 * `/gardening/routines` (`gardening-routines-page.ts` owns the list beside it):
 * the record, its read-only strategy, its three health readings, and its
 * garden-proposal counts (blizzard#547). Mounted by both of that route's children,
 * so the bare one renders the panel's own "nothing selected" empty state. D1 ships
 * no New/Edit affordance here.
 *
 * A container: it injects the routine, graph, trend, sweeps, scopes, and
 * proposal-counts queries and forwards a plain view model to the presentational
 * {@link FleetRoutinePanel}, which injects no query of its own. The routine and
 * graph reads are the same cache-keyed queries the list beside it already holds, so
 * resolving the routed routine independently costs no second fetch. The scopes read
 * renders the routine's related scope set, each marked whether it is this routine's
 * own default (D8). The proposal-counts read feeds a separate presentational
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
  styleUrl: './gardening-detail-host.css',
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

  /** The panel's fixed reporting window (AC 3, AC 4) — computed once at
   * construction, not re-derived per render; a page reload is what refreshes it. */
  private readonly window = defaultRoutineWindow(Date.now());

  private readonly routines = computed<readonly RoutineView[]>(() => this.routinesQuery.data() ?? []);
  private readonly graphs = computed<readonly GraphSummaryView[]>(() => this.graphsQuery.data() ?? []);

  /** The `routineName` route param, or `null` on the bare child route. Routines are
   * keyed by `name` (`hub/store/schema.py`'s `uq_routines_name`), not id. */
  private readonly routineNameParam = toSignal(
    this.route.paramMap.pipe(map((params) => params.get('routineName'))),
    { initialValue: null },
  );

  private readonly selectedRoutine = computed<RoutineView | null>(() => {
    const name = this.routineNameParam();
    return name === null ? null : (this.routines().find((r) => r.name === name) ?? null);
  });

  private readonly effectiveGraph = computed<GraphSummaryView | null>(() => {
    const routine = this.selectedRoutine();
    if (routine === null) return null;
    return effectiveGraphByName(this.graphs(), this.graphsQuery.isPending(), routine.graph_name);
  });

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
  private readonly overrideRetired = computed<boolean | null>(() => {
    const routine = this.selectedRoutine();
    if (routine === null) return null;
    const pending = this.pendingRoutineLifecycle().find((vars) => vars.routineId === routine.routine_id);
    return pending ? pending.retired : null;
  });

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

  private readonly strategy = computed<readonly StrategyStepVm[]>(() =>
    (this.graphQuery.data()?.nodes ?? []).map((n) => ({ name: n.name, prompt: n.prompt ?? null })),
  );

  private readonly measurements = computed<readonly MeasurementReadingVm[]>(() =>
    (this.sweepsQuery.data()?.measurements ?? []).map((m) => ({
      scopeSlug: m.scope_slug,
      producedAt: m.produced_at,
      measurement: m.measurement,
    })),
  );

  private readonly lastSwept = computed<readonly LastSweptRowVm[]>(() =>
    (this.sweepsQuery.data()?.last_swept ?? []).map((row) => ({
      scopeSlug: row.scope_slug,
      findingSetId: row.finding_set_id,
      producedAt: row.produced_at,
      revisionsLabel:
        Object.entries(row.revisions)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([repo, rev]) => `${repo}@${rev}`)
          .join(', ') || '—',
    })),
  );

  /** The selected routine's related scopes, each marked whether it is the routine's
   * own default (D8) — `null` until the routine-scopes read resolves
   * (D5). */
  private readonly relatedScopes = computed<readonly RelatedScopeVm[] | null>(() => {
    const slugs = this.scopesQuery.data();
    const routine = this.selectedRoutine();
    if (slugs === undefined || routine === null) return null;
    return slugs.map((slug) => ({ slug, isDefault: slug === routine.default_scope_slug }));
  });

  /** The proposal-counts table's own rows (blizzard#547) — mapped off the read's
   * `rows`, already scoped to the one selected routine by the query's own `routine`
   * filter. */
  protected readonly proposalCountsRows = computed<readonly ProposalCountsRowVm[]>(() =>
    (this.proposalCountsQuery.data()?.rows ?? []).map((row) => ({
      proposalClass: row.class,
      created: row.created,
      open: row.open,
      passed: row.passed,
      acceptedWithItem: row.accepted_with_item,
      acceptedWithoutItem: row.accepted_without_item,
    })),
  );

  /** The proposal-counts panel's own async state, independent of {@link panelState}
   * (`bzh:frontend-empty-state-gated`) — its own loading/error/empty resolve on this
   * one read, not gated on the record/graph reads the rest of the page renders
   * around. */
  protected readonly proposalCountsState = computed<KitAsyncStateValue>(() =>
    asyncState(this.proposalCountsQuery, (this.proposalCountsQuery.data()?.rows.length ?? 0) === 0),
  );

  protected readonly panelVm = computed<RoutinePanelVm | null>(() => {
    const routine = this.selectedRoutine();
    if (routine === null) return null;
    const trend = this.trendQuery.data();
    return {
      record: {
        name: routine.name,
        graphName: routine.graph_name,
        defaultScopeSlug: routine.default_scope_slug,
        defaultModel: routine.default_model ?? [],
        defaultEffort: routine.default_effort ?? null,
      },
      blockedReason: this.blocked() ? `graph ${routine.graph_name} has no effective mint` : null,
      strategy: this.strategy(),
      trend: trend
        ? {
            created: trend.periods.reduce((sum, p) => sum + p.created, 0),
            outflow: trend.periods.reduce((sum, p) => sum + p.outflow, 0),
            withdrawn: trend.periods.reduce((sum, p) => sum + p.withdrawn, 0),
            reopened: trend.periods.reduce((sum, p) => sum + p.reopened, 0),
          }
        : null,
      measurements: this.measurements(),
      lastSwept: this.lastSwept(),
      windowLabel: this.window.label,
      relatedScopes: this.relatedScopes(),
      retired: routine.retired ?? false,
      renderedRetired: this.overrideRetired() ?? (routine.retired ?? false),
    };
  });

  /** Gates only on what the record and `blocked` (D7) need — `routinesQuery` to know
   * there is a routine at all, `graphsQuery` to resolve `effectiveGraph`/`blocked`
   * without ever answering a graph-list failure as a confident "blocked". The record is
   * fully derivable from those two once resolved, so it is never held behind the
   * slower, independent `trendQuery`/`sweepsQuery`/`graphQuery` reads their own
   * sections already render around individually. */
  protected readonly panelState = computed<KitAsyncStateValue>(() => {
    if (this.routineNameParam() === null) return 'empty';
    if (this.selectedRoutine() === null) return asyncState(this.routinesQuery, true);
    if (this.graphsQuery.isPending()) return 'loading';
    if (this.graphsQuery.isError()) return 'error';
    return 'ready';
  });

  /** The routine currently running the dialog against — `null` closes it (blizzard#399
   * D6). Only {@link FleetRoutinePanel}'s own `run` output ever sets it, so it can only
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

  /** Whether the retire/enable mutation is in flight for this routine, threaded to
   * {@link FleetRoutinePanel}'s Re-enable/Retire buttons — only one of the two is ever
   * shown for the routine's current lifecycle state. */
  protected readonly lifecyclePending = computed(() => this.routineLifecycleMutation.isPending());

  protected onRetireRoutine(): void {
    const routineId = this.selectedRoutine()?.routine_id;
    if (routineId === undefined) return;
    this.lifecycleActionError.set(null);
    this.routineLifecycleMutation.mutate(
      { routineId, retired: true },
      { onError: (error: unknown) => this.lifecycleActionError.set(errorMessage(error, 'Retire failed.')) },
    );
  }

  protected onEnableRoutine(): void {
    const routineId = this.selectedRoutine()?.routine_id;
    if (routineId === undefined) return;
    this.lifecycleActionError.set(null);
    this.routineLifecycleMutation.mutate(
      { routineId, retired: false },
      { onError: (error: unknown) => this.lifecycleActionError.set(errorMessage(error, 'Enable failed.')) },
    );
  }
}

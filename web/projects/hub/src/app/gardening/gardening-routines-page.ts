import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import {
  asyncState,
  defaultRoutineWindow,
  FleetRoutineList,
  FleetRoutinePanel,
  injectHubGraphQuery,
  injectHubGraphsQuery,
  injectHubRoutineSweepsQuery,
  injectHubRoutineTrendQuery,
  injectHubRoutinesQuery,
  type GraphSummaryView,
  type KitAsyncStateValue,
  type LastSweptRowVm,
  type MeasurementReadingVm,
  type RoutineListRowVm,
  type RoutinePanelVm,
  type RoutineView,
  type StrategyStepVm,
} from 'fleet';

/**
 * The `/gardening/routines` sub-tab (blizzard#397, `plans/garden/user-interface.md`
 * §Declaring and running a routine) — the routine list, and the selected routine's
 * record, its read-only strategy, and the three health readings (D1 ships no New/Edit
 * affordance; run, scope authoring, and cross-routine scope health are each a later
 * issue's, per the issue's own Out of Scope note).
 *
 * A container: it injects the routine, graph, trend, and sweeps queries, derives
 * `blocked` (D7) off the same effective-graph resolution a run itself refuses on, and
 * forwards plain view models to the presentational {@link FleetRoutineList} and
 * {@link FleetRoutinePanel} — neither of which injects a query of its own.
 */
@Component({
  selector: 'app-gardening-routines-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetRoutineList, FleetRoutinePanel],
  templateUrl: './gardening-routines-page.html',
  styleUrl: './gardening-routines-page.css',
})
export class GardeningRoutinesPage {
  private readonly routinesQuery = injectHubRoutinesQuery();
  private readonly graphsQuery = injectHubGraphsQuery();

  /** The panel's fixed reporting window (AC 3, AC 4) — computed once at construction,
   * not re-derived per render; a page reload is what refreshes it. */
  private readonly window = defaultRoutineWindow(Date.now());

  private readonly routines = computed<readonly RoutineView[]>(() => this.routinesQuery.data() ?? []);

  /** The operator's explicit pick, `null` until one is made. */
  private readonly explicitSelection = signal<string | null>(null);

  /** The effective selection: the explicit pick if it still names a known routine,
   * else the first routine once the list has loaded — never a stale id from a routine
   * that no longer exists. */
  protected readonly selectedId = computed<string | null>(() => {
    const routines = this.routines();
    const explicit = this.explicitSelection();
    if (explicit !== null && routines.some((r) => r.routine_id === explicit)) return explicit;
    return routines[0]?.routine_id ?? null;
  });

  private readonly selectedRoutine = computed<RoutineView | null>(
    () => this.routines().find((r) => r.routine_id === this.selectedId()) ?? null,
  );

  /** The routine's effective graph (D7) — the same newest-non-retired-per-name
   * resolution `IReadGraphRepository.get_enabled_by_name` performs, so this can never
   * disagree with what a run itself refuses on. `null` while the graph list is still
   * loading, so {@link blocked} doesn't flash true before it resolves. */
  private readonly effectiveGraph = computed<GraphSummaryView | null>(() => {
    const routine = this.selectedRoutine();
    if (routine === null || this.graphsQuery.isPending()) return null;
    return (this.graphsQuery.data() ?? []).find((g) => g.name === routine.graph_name && g.effective) ?? null;
  });

  protected readonly blocked = computed<boolean>(
    () => this.selectedRoutine() !== null && !this.graphsQuery.isPending() && this.effectiveGraph() === null,
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

  protected readonly listRows = computed<readonly RoutineListRowVm[]>(() =>
    this.routines().map((r) => ({ routineId: r.routine_id, name: r.name, graphName: r.graph_name })),
  );

  protected readonly listState = computed<KitAsyncStateValue>(() =>
    asyncState(this.routinesQuery, this.routines().length === 0),
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

  protected readonly panelVm = computed<RoutinePanelVm | null>(() => {
    const routine = this.selectedRoutine();
    if (routine === null) return null;
    const trend = this.trendQuery.data();
    return {
      record: {
        routineId: routine.routine_id,
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
    };
  });

  /** Gates only on what the record and `blocked` (D7) need — `routinesQuery` to know
   * there is a routine at all, `graphsQuery` to resolve `effectiveGraph`/`blocked`
   * without ever answering a graph-list failure as a confident "blocked". The record is
   * fully derivable from those two once resolved, so it is never held behind the
   * slower, independent `trendQuery`/`sweepsQuery`/`graphQuery` reads their own
   * sections already render around individually. */
  protected readonly panelState = computed<KitAsyncStateValue>(() => {
    if (this.selectedRoutine() === null) return asyncState(this.routinesQuery, true);
    if (this.graphsQuery.isPending()) return 'loading';
    if (this.graphsQuery.isError()) return 'error';
    return 'ready';
  });

  protected select(routineId: string): void {
    this.explicitSelection.set(routineId);
  }
}

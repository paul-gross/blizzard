import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import {
  asyncState,
  defaultRoutineWindow,
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
 * the record, its read-only strategy, and its three health readings. Mounted by
 * both of that route's children, so the bare one renders the panel's own
 * "nothing selected" empty state. D1 ships no New/Edit affordance here.
 *
 * A container: it injects the routine, graph, trend, and sweeps queries and
 * forwards a plain view model to the presentational {@link FleetRoutinePanel},
 * which injects no query of its own. The routine and graph reads are the same
 * cache-keyed queries the list beside it already holds, so resolving the routed
 * routine independently costs no second fetch.
 *
 * The reporting window is this pane's alone — nothing in the list is cut to it —
 * so it is computed here, once, at construction.
 */
@Component({
  selector: 'app-gardening-routine-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetRoutinePanel, GardeningRunDialog],
  templateUrl: './gardening-routine-detail.html',
  styleUrl: './gardening-detail-host.css',
})
export class GardeningRoutineDetail {
  private readonly route = inject(ActivatedRoute);

  private readonly routinesQuery = injectHubRoutinesQuery();
  private readonly graphsQuery = injectHubGraphsQuery();

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
}

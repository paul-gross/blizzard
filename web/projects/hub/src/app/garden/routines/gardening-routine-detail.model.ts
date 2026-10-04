import {
  type AsyncStateQuery,
  type GardenProposalCountsView,
  type GardenSweepsView,
  type GraphSummaryView,
  type GraphView,
  type KitAsyncStateValue,
  type RoutineView,
  type TrendView,
  restingAsyncState,
} from 'fleet';
import { effectiveGraphByName } from './gardening-effective-graph';
import type { RoutineLifecycleVars } from './routine-lifecycle.mutations';
import type {
  LastSweptRowVm,
  MeasurementReadingVm,
  RelatedScopeVm,
  RoutinePanelVm,
  StrategyStepVm,
  TrendSummaryVm,
} from './routine-panel';
import type { ProposalCountsRowVm } from './routine-proposal-counts';

/** The routine `name` names, or `null` when `name` is `null` (the bare child route)
 * or names no loaded routine. Routines are keyed by `name`, unique per routine. */
export function routineByName(name: string | null, routines: readonly RoutineView[]): RoutineView | null {
  return name === null ? null : (routines.find((r) => r.name === name) ?? null);
}

/** The effective graph `routine` runs against — `null` with no routine, or while the
 * graph read is still pending (see {@link effectiveGraphByName}). */
export function routineEffectiveGraph(
  routine: RoutineView | null,
  graphs: readonly GraphSummaryView[],
  graphsPending: boolean,
): GraphSummaryView | null {
  if (routine === null) return null;
  return effectiveGraphByName(graphs, graphsPending, routine.graph_name);
}

/** `routine`'s `retired` flag as it will read once a pending Retire/Enable settles
 * (`bzh:frontend-pending-override`) — `null` with no routine or nothing pending for it. */
export function routineOverrideRetired(
  routine: RoutineView | null,
  pending: readonly RoutineLifecycleVars[],
): boolean | null {
  if (routine === null) return null;
  const vars = pending.find((v) => v.routineId === routine.routine_id);
  return vars ? vars.retired : null;
}

/** The read-only strategy steps of the routine's effective graph, in node order. */
export function strategySteps(graph: GraphView | undefined): readonly StrategyStepVm[] {
  return (graph?.nodes ?? []).map((n) => ({ name: n.name, prompt: n.prompt ?? null }));
}

/** The window's measurement readings, one per sweep that produced one. */
export function measurementReadings(sweeps: GardenSweepsView | undefined): readonly MeasurementReadingVm[] {
  return (sweeps?.measurements ?? []).map((m) => ({
    scopeSlug: m.scope_slug,
    producedAt: m.produced_at,
    measurement: m.measurement,
  }));
}

/** Each related scope's last sweep, its revisions rendered `repo@rev` in repo order —
 * `—` when the sweep pinned no revision. */
export function lastSweptRows(sweeps: GardenSweepsView | undefined): readonly LastSweptRowVm[] {
  return (sweeps?.last_swept ?? []).map((row) => ({
    scopeSlug: row.scope_slug,
    findingSetId: row.finding_set_id,
    producedAt: row.produced_at,
    revisionsLabel:
      Object.entries(row.revisions)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([repo, rev]) => `${repo}@${rev}`)
        .join(', ') || '—',
  }));
}

/** The routine's related scopes, each marked whether it is the routine's own default —
 * `null` until the related-scopes read resolves, or with no routine. */
export function relatedScopeRows(
  slugs: readonly string[] | undefined,
  routine: RoutineView | null,
): readonly RelatedScopeVm[] | null {
  if (slugs === undefined || routine === null) return null;
  return slugs.map((slug) => ({ slug, isDefault: slug === routine.default_scope_slug }));
}

/** The proposal-counts table's rows, one per origin and class. */
export function proposalCountsRows(counts: GardenProposalCountsView | undefined): readonly ProposalCountsRowVm[] {
  return (counts?.rows ?? []).map((row) => ({
    origin: row.origin,
    proposalClass: row.class,
    created: row.created,
    open: row.open,
    passed: row.passed,
    acceptedWithItem: row.accepted_with_item,
    acceptedWithoutItem: row.accepted_without_item,
  }));
}

/** The trend read summed across every period of the window. */
export function trendTotals(trend: TrendView): TrendSummaryVm {
  return {
    created: trend.periods.reduce((sum, p) => sum + p.created, 0),
    outflow: trend.periods.reduce((sum, p) => sum + p.outflow, 0),
    withdrawn: trend.periods.reduce((sum, p) => sum + p.withdrawn, 0),
    reopened: trend.periods.reduce((sum, p) => sum + p.reopened, 0),
  };
}

/** Everything {@link routinePanelVm} composes beside the routine itself. */
export interface RoutinePanelParts {
  readonly blocked: boolean;
  readonly strategy: readonly StrategyStepVm[];
  readonly trend: TrendView | undefined;
  readonly measurements: readonly MeasurementReadingVm[];
  readonly lastSwept: readonly LastSweptRowVm[];
  readonly windowLabel: string;
  readonly relatedScopes: readonly RelatedScopeVm[] | null;
  readonly overrideRetired: boolean | null;
}

/** The routine panel's view model — `null` with no routine selected. `renderedRetired`
 * reads the pending override while one names this routine, else its real flag. */
export function routinePanelVm(routine: RoutineView | null, parts: RoutinePanelParts): RoutinePanelVm | null {
  if (routine === null) return null;
  return {
    record: {
      name: routine.name,
      graphName: routine.graph_name,
      defaultScopeSlug: routine.default_scope_slug,
      defaultModel: routine.default_model ?? [],
      defaultEffort: routine.default_effort ?? null,
    },
    blockedReason: parts.blocked ? `graph ${routine.graph_name} has no effective mint` : null,
    strategy: parts.strategy,
    trend: parts.trend ? trendTotals(parts.trend) : null,
    measurements: parts.measurements,
    lastSwept: parts.lastSwept,
    windowLabel: parts.windowLabel,
    relatedScopes: parts.relatedScopes,
    retired: routine.retired ?? false,
    renderedRetired: parts.overrideRetired ?? (routine.retired ?? false),
  };
}

/** The routine panel's async state, gated only on what the record and its blocked
 * flag need: "nothing selected" rests as `'empty'`; an unresolved routine answers off
 * the routine read; a resolved one waits on the graph read, so a graph-list failure
 * never reads as a confident "blocked". */
export function routinePanelState(
  routineName: string | null,
  selected: RoutineView | null,
  routinesQuery: AsyncStateQuery,
  graphsPending: boolean,
  graphsError: boolean,
): KitAsyncStateValue {
  if (selected === null) return restingAsyncState(routineName === null, routinesQuery, true);
  if (graphsPending) return 'loading';
  if (graphsError) return 'error';
  return 'ready';
}

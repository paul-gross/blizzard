import { asyncState, type AsyncStateQuery, type GraphSummaryView, type RoutineView } from 'fleet';
import { isRoutineBlocked } from './gardening-effective-graph';
import type { RoutineListRowVm } from './routine-list';

/** The effective selection: `routineName` while it still names a loaded routine,
 * else `null` — so a stale or mistyped route param highlights no row. */
export function presentRoutineName(routineName: string | null, routines: readonly RoutineView[]): string | null {
  if (routineName === null) return null;
  return routines.some((r) => r.name === routineName) ? routineName : null;
}

/** The routine list's rows, each flagged blocked when its graph has no effective mint. A graphs read that is
 * pending or failed asserts nothing — no row is blocked until it has resolved. */
export function routineListRows(
  routines: readonly RoutineView[],
  graphs: readonly GraphSummaryView[],
  graphsQuery: AsyncStateQuery,
): readonly RoutineListRowVm[] {
  const graphsUnread = asyncState(graphsQuery, false) !== 'ready';
  return routines.map((r) => ({
    routineId: r.routine_id,
    name: r.name,
    graphName: r.graph_name,
    blocked: isRoutineBlocked(graphs, graphsUnread, r.graph_name),
    retired: r.retired ?? false,
  }));
}

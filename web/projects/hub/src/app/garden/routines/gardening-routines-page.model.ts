import type { GraphSummaryView, RoutineView } from 'fleet';
import { isRoutineBlocked } from './gardening-effective-graph';
import type { RoutineListRowVm } from './routine-list';

/** The effective selection: `routineName` while it still names a loaded routine,
 * else `null` — so a stale or mistyped route param highlights no row. */
export function presentRoutineName(routineName: string | null, routines: readonly RoutineView[]): string | null {
  if (routineName === null) return null;
  return routines.some((r) => r.name === routineName) ? routineName : null;
}

/** The routine list's rows, each flagged blocked when its graph has no effective mint. */
export function routineListRows(
  routines: readonly RoutineView[],
  graphs: readonly GraphSummaryView[],
  graphsPending: boolean,
): readonly RoutineListRowVm[] {
  return routines.map((r) => ({
    routineId: r.routine_id,
    name: r.name,
    graphName: r.graph_name,
    blocked: isRoutineBlocked(graphs, graphsPending, r.graph_name),
    retired: r.retired ?? false,
  }));
}

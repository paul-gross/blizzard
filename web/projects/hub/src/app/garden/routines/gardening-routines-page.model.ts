import { asyncState, type AsyncStateQuery, type CadenceView, type GraphSummaryView, type RoutineView } from 'fleet';
import { keptByRows } from './gardening-routine-detail.model';
import { isRoutineBlocked } from './gardening-effective-graph';
import type { RoutineListRowVm } from './routine-list';

/** The effective selection: `routineSlug` while it still names a loaded routine,
 * else `null` — so a stale or mistyped route param highlights no row. */
export function presentRoutineSlug(routineSlug: string | null, routines: readonly RoutineView[]): string | null {
  if (routineSlug === null) return null;
  return routines.some((r) => r.slug === routineSlug) ? routineSlug : null;
}

/** The routine list's rows, each flagged blocked when its graph has no effective mint, and kept when a cadence chore
 * keeps it. A graphs read that is pending or failed asserts nothing — no row is blocked until it has resolved; an
 * unread cadences list keeps no row. */
export function routineListRows(
  routines: readonly RoutineView[],
  graphs: readonly GraphSummaryView[],
  graphsQuery: AsyncStateQuery,
  cadences: readonly CadenceView[] | undefined,
): readonly RoutineListRowVm[] {
  const graphsUnread = asyncState(graphsQuery, false) !== 'ready';
  return routines.map((r) => ({
    routineId: r.routine_id,
    slug: r.slug,
    name: r.name,
    graphName: r.graph_name,
    blocked: isRoutineBlocked(graphs, graphsUnread, r.graph_name),
    retired: r.retired ?? false,
    kept: (keptByRows(cadences, r)?.length ?? 0) > 0,
  }));
}

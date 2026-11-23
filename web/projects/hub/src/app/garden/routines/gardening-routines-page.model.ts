import { asyncState, type AsyncStateQuery } from 'fleet';
import type { GraphSummary } from '../../graphs/graph.domain';
import type { Routine } from '../core/routine.domain';
import { isRoutineBlocked } from './gardening-effective-graph';
import type { RoutineListRowVm } from './routine-list';

/** The effective selection: `routineSlug` while it still names a loaded routine,
 * else `null` — so a stale or mistyped route param highlights no row. */
export function presentRoutineSlug(routineSlug: string | null, routines: readonly Routine[]): string | null {
  if (routineSlug === null) return null;
  return routines.some((r) => r.slug === routineSlug) ? routineSlug : null;
}

/** The routine list's rows, each flagged blocked when its graph has no effective mint, and kept when a cadence chore
 * keeps it — the routine's own `kept_by` says so. A graphs read that is pending or failed asserts nothing — no row is
 * blocked until it has resolved. */
export function routineListRows(
  routines: readonly Routine[],
  graphs: readonly GraphSummary[],
  graphsQuery: AsyncStateQuery,
): readonly RoutineListRowVm[] {
  const graphsUnread = asyncState(graphsQuery, false) !== 'ready';
  return routines.map((r) => ({
    routineId: r.id,
    slug: r.slug,
    name: r.name,
    graphName: r.graph,
    blocked: isRoutineBlocked(graphs, graphsUnread, r.graph),
    retired: r.retired,
    kept: r.keptBy.length > 0,
  }));
}

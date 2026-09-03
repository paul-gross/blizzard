import type { GraphSummaryView } from 'fleet';

/**
 * The effective graph for a routine's `graph_name` (blizzard#399 D7) — the same
 * newest-non-retired-per-name resolution `IReadGraphRepository.get_enabled_by_name`
 * performs, so the board can never disagree with what a run itself refuses on.
 *
 * A pure read over an already-fetched graph list rather than a method on either
 * component: the routine list needs it per row and the routine detail needs it for
 * the selected routine, and the two live on different sides of the route boundary
 * now (`gardening-routines-page.ts` / `gardening-routine-detail.ts`).
 *
 * `graphsPending` is the graph read's own `isPending()`. While it is true this
 * answers `null` — and {@link isRoutineBlocked} answers `false` — so a fresh load
 * never flashes every routine blocked before the graph list resolves.
 */
export function effectiveGraphByName(
  graphs: readonly GraphSummaryView[],
  graphsPending: boolean,
  graphName: string,
): GraphSummaryView | null {
  if (graphsPending) return null;
  return graphs.find((g) => g.name === graphName && g.effective) ?? null;
}

/** Whether a routine on `graphName` is blocked — no effective mint to run against. */
export function isRoutineBlocked(
  graphs: readonly GraphSummaryView[],
  graphsPending: boolean,
  graphName: string,
): boolean {
  return !graphsPending && effectiveGraphByName(graphs, graphsPending, graphName) === null;
}

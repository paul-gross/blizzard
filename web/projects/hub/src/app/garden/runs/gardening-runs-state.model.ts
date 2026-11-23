import type { DeliveredSetCounts, RunRow } from '../core/run.domain';
import { vocabularyText } from '../core/vocabulary.domain';
import type { Routine } from '../core/routine.domain';
import { routineDisplayName } from '../core/routine-display-name';
import type { RunListCountsVm, RunListRowVm } from './run-list';

/** A run's counts triple, summed across every set it delivered — `null` when it
 * delivered none, so the row renders no triple rather than a misleading `+0`. */
export function summedCounts(
  delivered: readonly DeliveredSetCounts[],
): RunListCountsVm | null {
  if (delivered.length === 0) return null;
  return delivered.reduce(
    (sum, set) => ({
      added: sum.added + set.addedCount,
      observed: sum.observed + set.observedCount,
      gone: sum.gone + set.goneCount,
    }),
    { added: 0, observed: 0, gone: 0 },
  );
}

/** The run list's rows, one per run in the read's order, each routine read by its display name. */
export function runListRows(
  runs: readonly RunRow[],
  routines: readonly Routine[] | undefined,
): readonly RunListRowVm[] {
  return runs.map((row) => ({
    chunkId: row.chunkId,
    routineName: routineDisplayName(row.routineName, routines),
    scopeSlug: row.scopeSlug,
    mode: vocabularyText(row.mode),
    mintedAt: row.mintedAt,
    laneStatus: row.laneStatus,
    escalated: row.escalation !== null,
    counts: summedCounts(row.delivered),
  }));
}

/** When `chunkId` was minted, off the matching list row — `null` when no row carries it. */
export function mintedAtFor(rows: readonly RunListRowVm[], chunkId: string): string | null {
  return rows.find((row) => row.chunkId === chunkId)?.mintedAt ?? null;
}

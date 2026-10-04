import type { RunRowView } from 'fleet';
import type { RunListCountsVm, RunListRowVm } from './run-list';

/** A run's counts triple, summed across every set it delivered — `null` when it
 * delivered none, so the row renders no triple rather than a misleading `+0`. */
export function summedCounts(
  delivered: readonly { added_count: number; observed_count: number; gone_count: number }[],
): RunListCountsVm | null {
  if (delivered.length === 0) return null;
  return delivered.reduce(
    (sum, set) => ({
      added: sum.added + set.added_count,
      observed: sum.observed + set.observed_count,
      gone: sum.gone + set.gone_count,
    }),
    { added: 0, observed: 0, gone: 0 },
  );
}

/** The run list's rows, one per run in the read's order. */
export function runListRows(runs: readonly RunRowView[]): readonly RunListRowVm[] {
  return runs.map((row) => ({
    chunkId: row.chunk_id,
    routineName: row.routine_name,
    scopeSlug: row.scope_slug,
    mode: row.mode,
    mintedAt: row.minted_at,
    outcome: row.outcome,
    escalated: row.escalation !== null,
    counts: summedCounts(row.delivered),
  }));
}

/** When `chunkId` was minted, off the matching list row — `null` when no row carries it. */
export function mintedAtFor(rows: readonly RunListRowVm[], chunkId: string): string | null {
  return rows.find((row) => row.chunkId === chunkId)?.mintedAt ?? null;
}

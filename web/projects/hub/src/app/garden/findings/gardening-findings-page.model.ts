import { type FindingView } from 'fleet';
import { type FindingListRowVm } from '../core/finding-list';

/** Pared to what the 320px master column renders — `observed_count` and
 * `introduced` show in the detail pane once the row is picked, not here;
 * `last_seen_at` rides both, since the row's own fourth line is the most recent
 * observation. Only the dimension the active filter leaves unnamed shows on the
 * row: a bucket widened to every routine or every scope needs each row to
 * say which it came from, but a bucket already filtered to one doesn't need it
 * repeated on every row. `source`/`severity`/`raised_by_chunk_id`
 * ride every row verbatim — unlike routine/scope they carry no filter-dependent
 * `null`-out. */
export function findingListRows(
  bucket: readonly FindingView[],
  selectedRoutine: string | null,
  selectedScope: string | null,
): readonly FindingListRowVm[] {
  return bucket.map((f) => ({
    findingId: f.finding_id,
    findingClass: f.class,
    locus: f.locus,
    summary: f.summary,
    state: f.state,
    exit: f.exit ?? null,
    lastSeenAt: f.last_seen_at,
    routineName: selectedRoutine === null ? (f.routine_name ?? null) : null,
    scopeSlug: selectedScope === null ? f.scope_slug : null,
    source: f.source ?? 'routine',
    severity: f.severity ?? null,
    raisedByChunkId: f.raised_by_chunk_id ?? null,
  }));
}

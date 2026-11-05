import { type FindingView, type RoutineView } from 'fleet';
import { routineDisplayName } from '../core/routine-display-name';
import { type FindingListRowVm } from '../core/finding-list';

/** Maps the bucket to {@link FindingListRowVm}s. `routineName`/`scopeSlug` are
 * `null` where the active filter already names that dimension; a routine reads as its display name. */
export function findingListRows(
  bucket: readonly FindingView[],
  selectedRoutine: string | null,
  selectedScope: string | null,
  routines: readonly RoutineView[] | undefined,
): readonly FindingListRowVm[] {
  return bucket.map((f) => ({
    findingId: f.finding_id,
    findingClass: f.class,
    locus: f.locus,
    summary: f.summary,
    state: f.state,
    exit: f.exit ?? null,
    lastSeenAt: f.last_seen_at,
    routineName: selectedRoutine === null && f.routine_name ? routineDisplayName(f.routine_name, routines) : null,
    scopeSlug: selectedScope === null ? f.scope_slug : null,
    source: f.source ?? 'routine',
    severity: f.severity ?? null,
    raisedByChunkId: f.raised_by_chunk_id ?? null,
  }));
}

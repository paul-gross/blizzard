import type { Finding } from '../core/finding.domain';
import type { Routine } from '../core/routine.domain';
import { routineDisplayName } from '../core/routine-display-name';
import { type FindingListRowVm } from '../core/finding-list';

/** Maps the bucket to {@link FindingListRowVm}s. `routineName`/`scopeSlug` are
 * `null` where the active filter already names that dimension; a routine reads as its display name. */
export function findingListRows(
  bucket: readonly Finding[],
  selectedRoutine: string | null,
  selectedScope: string | null,
  routines: readonly Routine[] | undefined,
): readonly FindingListRowVm[] {
  return bucket.map((f) => ({
    findingId: f.id,
    findingClass: f.findingClass,
    locus: f.locus,
    summary: f.summary,
    state: f.state,
    exit: f.exit,
    lastSeenAt: f.lastSeenAt,
    routineName: selectedRoutine === null && f.routineSlug ? routineDisplayName(f.routineSlug, routines) : null,
    scopeSlug: selectedScope === null ? f.scopeSlug : null,
    source: f.source,
    severity: f.severity,
    raisedByChunkId: f.raisedByChunkId,
  }));
}

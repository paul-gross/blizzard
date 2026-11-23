import type { RunDelta } from '../core/run.domain';
import type { Routine } from '../core/routine.domain';
import { routineDisplayName } from '../core/routine-display-name';
import { type RunDeltaVm } from './run-delta';

/** One `revisions` map, rendered `repo@revision, …` — `gardening-routine-detail.ts`'s
 * own `lastSwept` reduction, sorted for a deterministic label. */
function revisionsLabel(revisions: Readonly<Record<string, string>>): string {
  return (
    Object.entries(revisions)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([repo, rev]) => `${repo}@${rev}`)
      .join(', ') || '—'
  );
}

/** The selected run's delta as the view model {@link RunDeltaVm} renders — `null` until the read resolves.
 * `mintedAtFor` supplies the run's minted moment, which the delta read does not carry; `routines` resolves its routine's
 * slug to the display name. */
export function runDeltaVm(
  delta: RunDelta | undefined,
  mintedAtFor: (chunkId: string) => string | null,
  routines: readonly Routine[] | undefined,
): RunDeltaVm | null {
  if (delta === undefined) return null;
  return {
    chunkId: delta.chunkId,
    routineName: routineDisplayName(delta.routineSlug, routines),
    scopeSlug: delta.scopeSlug,
    mintedAt: mintedAtFor(delta.chunkId),
    escalation: delta.escalation,
    cadence: delta.cadence,
    sets: delta.sets.map((set) => ({
      findingSetId: set.findingSetId,
      revisionsLabel: revisionsLabel(set.revisions),
      measurement: set.measurement,
      added: set.added,
      observed: set.observed,
      gone: set.gone,
    })),
  };
}

import { type RoutineView, type RunDeltaView } from 'fleet';
import { routineDisplayName } from '../core/routine-display-name';
import { type RunDeltaVm } from './run-delta';

/** One `revisions` map, rendered `repo@revision, …` — `gardening-routine-detail.ts`'s
 * own `lastSwept` reduction, sorted for a deterministic label. */
function revisionsLabel(revisions: Record<string, string>): string {
  return (
    Object.entries(revisions)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([repo, rev]) => `${repo}@${rev}`)
      .join(', ') || '—'
  );
}

/** The selected run's delta as the view model {@link RunDeltaVm} renders — `null` until the read resolves.
 * `mintedAtFor` supplies the run's `minted_at`, which the delta read does not carry; `routines` resolves its routine's
 * slug to the display name. */
export function runDeltaVm(
  delta: RunDeltaView | undefined,
  mintedAtFor: (chunkId: string) => string | null,
  routines: readonly RoutineView[] | undefined,
): RunDeltaVm | null {
  if (delta === undefined) return null;
  return {
    chunkId: delta.chunk_id,
    routineName: routineDisplayName(delta.routine_name, routines),
    scopeSlug: delta.scope_slug,
    mintedAt: mintedAtFor(delta.chunk_id),
    escalation:
      delta.escalation === null
        ? null
        : {
            nodeName: delta.escalation.node_name,
            takeoverCommand: delta.escalation.takeover_command,
            wrappedTakeoverCommand: delta.escalation.wrapped_takeover_command,
          },
    cadence:
      delta.cadence === null
        ? null
        : { slug: delta.cadence.cadence_slug, name: delta.cadence.cadence_name, choreKey: delta.cadence.chore_key },
    sets: delta.sets.map((set) => ({
      findingSetId: set.finding_set_id,
      revisionsLabel: revisionsLabel(set.revisions),
      measurement: set.measurement,
      added: set.added.map((a) => ({
        findingId: a.finding_id,
        findingClass: a.class,
        locus: a.locus,
        summary: a.summary,
        introduced: a.introduced,
      })),
      observed: set.observed.map((o) => ({
        findingId: o.finding_id,
        findingClass: o.class,
        locus: o.locus,
        summary: o.summary,
      })),
      gone: set.gone.map((g) => ({ findingId: g.finding_id, note: g.note })),
    })),
  };
}

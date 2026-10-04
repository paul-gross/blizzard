import { type RunDeltaView } from 'fleet';
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
 * `mintedAtFor` supplies the run's `minted_at`, which the delta read does not carry. */
export function runDeltaVm(
  delta: RunDeltaView | undefined,
  mintedAtFor: (chunkId: string) => string | null,
): RunDeltaVm | null {
  if (delta === undefined) return null;
  return {
    chunkId: delta.chunk_id,
    routineName: delta.routine_name,
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

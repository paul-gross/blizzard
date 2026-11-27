import { compactRef, runnerDisplayName } from 'fleet/core';
import { type LoggedEvent } from '../../core/live/hub-live-updates';

/** A `chunk-changed` frame shaped into the Activity feed's two-line block. */
export interface ChunkChangeSummary {
  /** Line 1 — the chunk shortname and the fact's cause. */
  readonly transition: string;
  /** Line 2 — who acted: the runner's display name, e.g. `atlas/r-claude`, or, on a
   * runnerless deletion, the deleting actor; omitted otherwise. */
  readonly runner?: string;
}

/**
 * Shape a `chunk-changed` frame into the block row's two lines. The live frame's state
 * nodes may differ from this fact's transition endpoints.
 * `hostNames` resolves a runner id to its host.
 */
export function summarizeChunkChange(data: LoggedEvent['data'], hostNames: ReadonlyMap<string, string>): ChunkChangeSummary {
  const cause = data.cause === 'node-completed' ? 'completed' : data.cause === 'hub-advanced' ? 'transitioned' : data.cause;
  const reason = data.cause === 'released' && data.reason ? ` (${data.reason})` : '';
  const edge = (data.cause === 'node-completed' || data.cause === 'hub-advanced') && data.transition_from && data.transition_to
    ? ` ${data.transition_from} → ${data.transition_to}` : '';
  const summary: ChunkChangeSummary = { transition: `${compactRef(data.chunk_id ?? '—')}${cause ? ` ${cause}` : ''}${edge}${reason}` };
  if (data.runner_id) return { ...summary, runner: runnerDisplayName(data.runner_id, data.runner_name, hostNames.get(data.runner_id)) };
  if (data.cause === 'deleted' && data.by) return { ...summary, runner: data.by };
  return summary;
}

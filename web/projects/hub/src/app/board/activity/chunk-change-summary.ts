import { compactRef, hubApi, type LoggedEvent, runnerDisplayName } from 'fleet';

const { ChunkChangeCause } = hubApi;

/** A `chunk-changed` frame shaped into the Activity feed's two-line block. */
export interface ChunkChangeSummary {
  /** Line 1 — the chunk shortname and its transition, e.g. `C-1RJ1 review → failed → build`. */
  readonly transition: string;
  /** Line 2 — who acted: the runner's display name, e.g. `atlas/r-claude`, or, on a
   * runnerless deletion, the deleting actor; omitted otherwise. */
  readonly runner?: string;
}

/**
 * Shape a `chunk-changed` frame into the block row's two lines. Each absent segment of
 * `transition` is dropped along with its arrow, so a frame carrying neither node reads
 * `C-1NWW → running`. `hostNames` resolves a runner id to its host, so the runner reads `host/name`. The rendered shapes are pinned by `chunk-change-summary.spec.ts`.
 */
export function summarizeChunkChange(data: LoggedEvent['data'], hostNames: ReadonlyMap<string, string>): ChunkChangeSummary {
  if (data.cause === ChunkChangeCause.CLAIMED) {
    return {
      transition: `${compactRef(data.chunk_id ?? '—')} claimed`,
      ...(data.runner_id ? { runner: runnerDisplayName(data.runner_id, data.runner_name, hostNames.get(data.runner_id)) } : {}),
    };
  }
  const segments: string[] = [compactRef(data.chunk_id ?? '—')];
  if (data.prev_node) segments.push(data.prev_node);
  if (data.status) segments.push('→', data.status);
  if (data.node) segments.push('→', data.node);
  const summary: ChunkChangeSummary = { transition: segments.join(' ') };
  if (data.runner_id) return { ...summary, runner: runnerDisplayName(data.runner_id, data.runner_name, hostNames.get(data.runner_id)) };
  if (data.cause === ChunkChangeCause.DELETED && data.by) return { ...summary, runner: data.by };
  return summary;
}

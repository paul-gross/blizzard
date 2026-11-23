import type { ChunkCountsView, ChunkStatus } from '../api/hub';
import { type KnownLaneStatus, type LaneChunk, type LaneCounts, type LaneStatus } from './chunk-lanes';
import { openLookup } from './open-lookup';

/** Translate the wire vocabulary while retaining a newer server's unrecognized spelling. */
export const laneStatus = openLookup<ChunkStatus, LaneStatus>({
  not_ready: { kind: 'not_ready' },
  ready: { kind: 'ready' },
  running: { kind: 'running' },
  delivering: { kind: 'delivering' },
  waiting_on_human: { kind: 'waiting_on_human' },
  needs_human: { kind: 'needs_human' },
  paused: { kind: 'paused' },
  stopped: { kind: 'stopped' },
  done: { kind: 'done' },
}, (raw) => ({ kind: 'unknown', raw }));

export function laneChunk(chunk: { readonly status: string; readonly terminal?: boolean }): LaneChunk {
  return { status: laneStatus(chunk.status), terminal: chunk.terminal };
}

export function laneCountInput(counts: ChunkCountsView): LaneCounts {
  const statuses: Record<KnownLaneStatus, number> = {
    not_ready: counts.not_ready, ready: counts.ready, running: counts.running, delivering: counts.delivering,
    waiting_on_human: counts.waiting_on_human, needs_human: counts.needs_human, paused: counts.paused,
    stopped: counts.stopped, done: counts.done,
  };
  return { statuses, terminal: counts.terminal, total: counts.total };
}

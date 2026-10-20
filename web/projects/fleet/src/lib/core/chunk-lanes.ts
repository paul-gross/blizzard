import type { ChunkCountsView, ChunkStatus, ChunkSummary } from '../api/hub';
import type { Tone } from '../kit/tone';

/** The board's lanes, left → right: the backlog, the ready queue, then dispatch → done. */
export interface Lane {
  readonly key: string;
  /** The board column's engraved heading. */
  readonly label: string;
  /** The titlebar stat cell's label — the same lane, named for a count rather than a column. */
  readonly headerLabel: string;
}

export const LANES: readonly Lane[] = [
  { key: 'notready', label: 'BACKLOG', headerLabel: 'Backlog' },
  { key: 'ready', label: 'READY', headerLabel: 'Ready' },
  { key: 'waiting', label: 'WAIT/HUMAN', headerLabel: 'Waiting' },
  { key: 'needs', label: 'NEEDS HUMAN', headerLabel: 'Needs human' },
  { key: 'running', label: 'RUNNING', headerLabel: 'Running' },
  { key: 'done', label: 'DONE', headerLabel: 'Done' },
];

/** The DONE lane's key — where every chunk the hub flags `terminal` sits. */
export const DONE_LANE = 'done';

/**
 * Every chunk status folded onto its board lane. DONE membership is the hub's `terminal`
 * flag, read by {@link chunkLane} and {@link laneCounts}; the `stopped` and `done` entries
 * only place a summary that omits the optional `terminal` wire field. The map stays total
 * over the generated `ChunkStatus` so a new status fails to compile here until it is placed.
 *
 * The transient `delivering` shows under RUNNING. `paused` shares WAIT/HUMAN with
 * `waiting_on_human`: that is the lane for work stopped pending a human, which is what
 * an operator's pause is. The BACKLOG lane's key is `notready`, independent of its
 * rendered labels.
 */
export const STATUS_LANE: Record<ChunkStatus, string> = {
  not_ready: 'notready',
  ready: 'ready',
  running: 'running',
  delivering: 'running',
  waiting_on_human: 'waiting',
  needs_human: 'needs',
  paused: 'waiting',
  stopped: DONE_LANE,
  done: DONE_LANE,
};

/** The board lane a chunk sits in: DONE when the hub flags it `terminal`, else its
 * status's {@link STATUS_LANE} placement — which is how a summary that omits `terminal`
 * still lands a `stopped` or `done` chunk in DONE. */
export function chunkLane(chunk: Pick<ChunkSummary, 'status' | 'terminal'>): string {
  return chunk.terminal === true ? DONE_LANE : STATUS_LANE[chunk.status];
}

/**
 * The all-time per-lane counts. DONE is the hub's own `terminal` count; every other
 * lane sums the per-status counts {@link STATUS_LANE} places there, skipping the
 * statuses it places in DONE. Every lane is keyed, zero when empty.
 */
export function laneCounts(counts: ChunkCountsView): ReadonlyMap<string, number> {
  const perLane = new Map<string, number>(LANES.map((lane) => [lane.key, 0]));
  for (const status of Object.keys(STATUS_LANE) as ChunkStatus[]) {
    const lane = STATUS_LANE[status];
    if (lane === DONE_LANE) continue;
    perLane.set(lane, (perLane.get(lane) ?? 0) + counts[status]);
  }
  perLane.set(DONE_LANE, counts.terminal);
  return perLane;
}

/**
 * Every chunk status folded onto the shared {@link Tone} vocabulary — the fleet-side
 * half of the status-to-tone mapping (the other half is `deriveMachineChunkStatus`).
 */
export const STATUS_TONE: Record<ChunkStatus, Tone> = {
  not_ready: 'idle',
  ready: 'idle',
  running: 'running',
  delivering: 'running',
  waiting_on_human: 'waiting',
  needs_human: 'needs',
  paused: 'waiting',
  stopped: 'done',
  done: 'done',
};

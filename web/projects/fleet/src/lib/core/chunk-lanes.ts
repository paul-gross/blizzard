import type { ChunkCountsView, ChunkStatus } from '../api/hub';
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
  { key: 'running', label: 'RUNNING', headerLabel: 'Running' },
  { key: 'waiting', label: 'WAIT/HUMAN', headerLabel: 'Waiting' },
  { key: 'needs', label: 'NEEDS HUMAN', headerLabel: 'Needs human' },
  { key: 'done', label: 'DONE', headerLabel: 'Done' },
];

/**
 * Every chunk status folded onto its board lane — the single owner of that
 * fold, because the board and the titlebar both render it and must not disagree.
 *
 * The transient `delivering` shows under RUNNING and the terminal `stopped` under
 * DONE. `paused` shares WAIT/HUMAN with `waiting_on_human`: that is the lane for work
 * stopped pending a human, which is what an operator's pause is. `ready` has its own
 * READY lane, so every status maps to a lane and a chunk shows in exactly one place.
 * The BACKLOG lane's key is `notready`: the key is a selector consumed by styles,
 * specs, and e2e locators, independent of its rendered labels.
 *
 * Typed `Record<ChunkStatus, …>` deliberately: a new status added to the wire is then
 * a compile error here — the one place that has to decide where it belongs — instead
 * of silently vanishing from a surface that forgot to list it.
 */
export const STATUS_LANE: Record<ChunkStatus, string> = {
  not_ready: 'notready',
  ready: 'ready',
  running: 'running',
  delivering: 'running',
  waiting_on_human: 'waiting',
  needs_human: 'needs',
  paused: 'waiting',
  stopped: 'done',
  done: 'done',
};

/**
 * The all-time per-lane counts — the hub's per-status counts folded through
 * {@link STATUS_LANE}, the one fold the titlebar and the board columns both render from
 * so they cannot disagree. Every lane is keyed, zero when empty.
 */
export function laneCounts(counts: ChunkCountsView): ReadonlyMap<string, number> {
  const perLane = new Map<string, number>(LANES.map((lane) => [lane.key, 0]));
  for (const status of Object.keys(STATUS_LANE) as ChunkStatus[]) {
    const lane = STATUS_LANE[status];
    perLane.set(lane, (perLane.get(lane) ?? 0) + counts[status]);
  }
  return perLane;
}

/**
 * Every chunk status folded onto the shared {@link Tone} vocabulary —
 * the fleet-side half of the status-to-tone mapping (the other half is
 * `deriveMachineChunkStatus`). Grouped by the same lane intent as {@link STATUS_LANE}:
 * live work reads `running`, human-waiting reads `waiting`, a blocking escalation
 * reads `needs`, and a landed/backlog status reads `done`/`idle`.
 *
 * Typed `Record<ChunkStatus, Tone>` for the same reason as `STATUS_LANE`: a new wire
 * status is then a compile error here instead of silently missing a color.
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

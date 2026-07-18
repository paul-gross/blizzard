import type { ChunkStatus } from './api/hub';
import type { Tone } from './kit/tone';

/** The board's lanes, left → right: the not-ready backlog, then dispatch → done. */
export interface Lane {
  readonly key: string;
  /** The board column's engraved heading. */
  readonly label: string;
  /** The titlebar stat cell's label — the same lane, named for a count rather than a column. */
  readonly headerLabel: string;
}

export const LANES: readonly Lane[] = [
  { key: 'notready', label: 'NOT READY', headerLabel: 'Not ready' },
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
 * stopped pending a human, which is what an operator's pause is. `ready` maps to **no**
 * lane (`null`): the ready queue lives in the left rail
 * (fleet-queue-panel), so a ready chunk shows there and never also as a board card
 * (issue #22) — which makes `null` mean "in the rail, not on the board", and the
 * titlebar counts its Ready cell off exactly that rather than re-naming the status.
 *
 * Typed `Record<ChunkStatus, …>` deliberately: a new status added to the wire is then
 * a compile error here — the one place that has to decide where it belongs — instead
 * of silently vanishing from a surface that forgot to list it.
 */
export const STATUS_LANE: Record<ChunkStatus, string | null> = {
  not_ready: 'notready',
  ready: null,
  running: 'running',
  delivering: 'running',
  waiting_on_human: 'waiting',
  needs_human: 'needs',
  paused: 'waiting',
  stopped: 'done',
  done: 'done',
};

/** The lane a chunk's status belongs to, or `null` when it belongs to the ready rail. */
export function laneFor(status: ChunkStatus): string | null {
  return STATUS_LANE[status];
}

/**
 * Every chunk status folded onto the shared {@link Tone} vocabulary (issue #81) —
 * the fleet-side half of "one status-to-tone mapping consumed by both libraries";
 * `local-panel`'s `deriveMachineChunkStatus` (`chunk-status.ts`) is the other half,
 * folding the runner's own lease-state derivation onto the same `Tone` union rather
 * than inventing a second one. Grouped by the same lane intent as {@link STATUS_LANE}:
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

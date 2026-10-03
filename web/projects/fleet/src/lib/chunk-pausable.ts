import type { ChunkStatus } from './api/hub';

/** Statuses the hub's `PauseService` refuses to pause (`ChunkNotPausable`), mirrored
 * here so neither app's dock ever offers a Pause the server would answer with a 409 —
 * the hub board's dock header and the runner panel's dock both gate their control on
 * this one table. A terminal or mid-delivery chunk has no work to stop.
 *
 * `paused` is deliberately **absent**: whether a chunk is already paused is not a
 * question `status` can answer (PAUSED derives below the human-gated states), so it is
 * never asked here — each dock owns that half by reading the `pause` fact. */
export const NOT_PAUSABLE: ReadonlySet<ChunkStatus> = new Set<ChunkStatus>(['done', 'stopped', 'delivering']);

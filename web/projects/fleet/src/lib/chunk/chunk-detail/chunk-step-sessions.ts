import { InjectionToken, type Signal, inject, signal } from '@angular/core';

/**
 * What a daemon knows of the agent session that worked one node-step — the environment it
 * held, its process, when it started and ended, and how it ended in operator wording.
 * `endedAt`/`ending` are `null` while the session is still live.
 */
export interface ChunkStepSession {
  readonly environment: string | null;
  readonly pid: number | null;
  readonly startedAt: string;
  readonly endedAt: string | null;
  readonly ending: string | null;
}

/**
 * The routed chunk's step sessions, keyed by `nodeStepKey(nodeId, epoch)` — the Node history
 * list's per-step enrichment. A neutral seam: the runner app provides it from its own record of
 * the chunk's sessions, and an app that keeps none (the hub's) provides nothing, so its rows
 * render unchanged.
 */
export const CHUNK_STEP_SESSIONS = new InjectionToken<Signal<ReadonlyMap<string, ChunkStepSession>>>(
  'fleet.CHUNK_STEP_SESSIONS',
  {
    providedIn: 'root',
    factory: () => signal<ReadonlyMap<string, ChunkStepSession>>(new Map()).asReadonly(),
  },
);

/** The routed chunk's step sessions now — {@link CHUNK_STEP_SESSIONS}'s value. */
export function injectChunkStepSessions(): Signal<ReadonlyMap<string, ChunkStepSession>> {
  return inject(CHUNK_STEP_SESSIONS);
}

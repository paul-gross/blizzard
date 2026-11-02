import { signal, type WritableSignal } from '@angular/core';

/** The verbs a {@link ChunkControl} fires. */
export type ChunkControlVerb = 'pause' | 'resume' | 'detach' | 'complete' | 'delete';

/** A `ChunkControl` test double (structurally typed — the testing unit imports no feature): `canControl` and each pending flag are writable
 * signals (pending is one flag per verb, whatever the chunk id), and every fired action
 * lands on `calls` as `[verb, chunkId]`. A fired delete runs its `onDeleted` at once. */
export interface ChunkControlStub {
  readonly canControl: WritableSignal<boolean>;
  pausePending(chunkId: string): boolean;
  detachPending(chunkId: string): boolean;
  completePending(chunkId: string): boolean;
  deletePending(chunkId: string): boolean;
  pause(chunkId: string): void;
  resume(chunkId: string): void;
  detach(chunkId: string): void;
  complete(chunkId: string): void;
  delete(chunkId: string, onDeleted?: () => void): void;
  readonly pending: Record<'pause' | 'detach' | 'complete' | 'delete', WritableSignal<boolean>>;
  readonly calls: [ChunkControlVerb, string][];
}

export function chunkControlStub(canControl = true): ChunkControlStub {
  const pending = { pause: signal(false), detach: signal(false), complete: signal(false), delete: signal(false) };
  const calls: [ChunkControlVerb, string][] = [];
  return {
    canControl: signal(canControl),
    pending,
    calls,
    pausePending: () => pending.pause(),
    detachPending: () => pending.detach(),
    completePending: () => pending.complete(),
    deletePending: () => pending.delete(),
    pause: (chunkId) => void calls.push(['pause', chunkId]),
    resume: (chunkId) => void calls.push(['resume', chunkId]),
    detach: (chunkId) => void calls.push(['detach', chunkId]),
    complete: (chunkId) => void calls.push(['complete', chunkId]),
    delete: (chunkId, onDeleted) => {
      calls.push(['delete', chunkId]);
      onDeleted?.();
    },
  };
}

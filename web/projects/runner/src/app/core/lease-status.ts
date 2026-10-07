import { type Tone, runnerApi } from 'fleet';

/** A lease's own derived label and {@link Tone} — the shared vocabulary
 * (`fleet/lib/kit/tone.ts`), so every surface that renders a lease state reads
 * the same colour for it. */
export interface LeaseStatus {
  readonly label: string;
  readonly tone: Tone;
}

/**
 * The lease-state → label/tone fold, read off the server-derived `state` alone.
 * The machine panel's lease row renders it directly; `deriveMachineChunkStatus`
 * (`board/chunk-list/chunk-status.ts`) falls through to it once no chunk-level
 * human fact outranks the lease. It lives in the kernel because both the
 * `machine` and `board` units read it and `machine` may not import `board`.
 */
export function deriveLeaseStatus(lease: runnerApi.LeaseView): LeaseStatus {
  switch (lease.state) {
    case 'running':
      return { label: 'RUNNING', tone: 'running' };
    case 'stale':
      return { label: 'STALE', tone: 'stale' };
    case 'parked':
      return { label: 'PARKED', tone: 'waiting' };
    case 'backing-off':
      return { label: 'BACKING OFF', tone: 'waiting' };
    case 'spawning':
      return { label: 'SPAWNING', tone: 'spawning' };
    case 'exited':
      return { label: 'EXITED', tone: 'idle' };
    case 'closed':
      // `transitioned` is the one healthy closure (the node step completed and
      // the chunk moved on) — the rest (`failed`/`reaped`/`released`/…) read dim.
      return lease.closure_reason === runnerApi.LeaseClosureReason.TRANSITIONED
        ? { label: 'TRANSITIONED', tone: 'done' }
        : { label: `CLOSED · ${(lease.closure_reason ?? 'unknown').toUpperCase()}`, tone: 'idle' };
  }
}

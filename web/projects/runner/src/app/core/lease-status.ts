import { unlisted } from 'fleet/core';
import { type Tone } from 'fleet/kit';

import type { Lease } from './lease.domain';

import { sessionEnding } from './worker-session';

/** A lease's own derived label and {@link Tone} — the shared vocabulary
 * (`fleet/lib/kit/tone.ts`), so every surface that renders a lease state reads
 * the same colour for it. */
export interface LeaseStatus {
  readonly label: string;
  readonly tone: Tone;
}

/**
 * The lease-state → label/tone map over the lease's server-derived `state` and
 * `closure_healthy`; a state this build does not know reads raw. The worker line renders it directly;
 * `deriveMachineChunkStatus` (`chunk-status.ts`) falls through to it once no
 * chunk-level human fact outranks the lease. It lives in the kernel because both the
 * `runners` and `board` units read it and `runners` may not import `board`.
 */
export function deriveLeaseStatus(lease: Lease): LeaseStatus {
  const state = lease.state;
  // A state this build does not know reads raw, in the inert tone.
  if (typeof state !== 'string') return { label: state.raw.toUpperCase(), tone: 'idle' };
  switch (state) {
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
      return { label: 'WORKER EXITED', tone: 'idle' };
    case 'closed':
      // A healthy closure (the node step completed and the chunk moved on) reads done;
      // the rest read dim, worded by how the session ended.
      return lease.closure_healthy
        ? { label: 'COMPLETED', tone: 'done' }
        : { label: `ENDED · ${(sessionEnding(lease) ?? 'unknown').toUpperCase()}`, tone: 'idle' };
    default:
      return { label: unlisted(state).toUpperCase(), tone: 'idle' };
  }
}

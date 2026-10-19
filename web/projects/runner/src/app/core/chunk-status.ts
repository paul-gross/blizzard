import type { Tone, runnerApi } from 'fleet';

import { deriveLeaseStatus } from './lease-status';

/**
 * The machine-side derived status of a chunk this runner holds — folded at
 * read time from the chunk's newest lease plus the runner's own park/escalation/
 * takeover facts, never stored (`bzh:facts-not-status`). The runner has no
 * `ChunkStatus` of its own (that enum is the hub's); this is the local panel's
 * projection of the same idea from the facts this box holds.
 *
 * `tone` is the shared {@link Tone} vocabulary (`fleet/lib/kit/tone.ts`) —
 * matching the hub board's status→color scheme (board-shell/chunk-detail-panel): live
 * work amber, human-blocked red, human-waiting amber-hi, landed green, spawning cyan,
 * spent rows dim.
 */
export interface MachineChunkStatus {
  readonly label: string;
  readonly tone: Tone;
}

/**
 * The wire's `display_status` → label/tone map. The runner folds an open takeover
 * (a human is in the session *now*), then an escalation (blocked on a human), then an
 * open ask (waiting on one) over the lease's own state; a lease-state status falls
 * through to {@link deriveLeaseStatus}.
 */
export function deriveMachineChunkStatus(lease: runnerApi.LeaseView): MachineChunkStatus {
  switch (lease.display_status) {
    case 'human-in-session':
      return { label: 'HUMAN IN SESSION', tone: 'takeover' };
    case 'needs-human':
      return { label: 'NEEDS HUMAN', tone: 'needs' };
    case 'waiting-ask':
      return { label: 'WAITING · ASK', tone: 'waiting' };
    default:
      return deriveLeaseStatus(lease);
  }
}

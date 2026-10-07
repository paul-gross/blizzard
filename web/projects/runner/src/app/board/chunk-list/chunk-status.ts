import type { Tone, runnerApi } from 'fleet';

import { deriveLeaseStatus } from '../../core/lease-status';

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

/** The facts the fold reads beyond the lease itself. */
export interface MachineChunkFacts {
  readonly escalatedChunkIds: ReadonlySet<string>;
  readonly takeoverChunkIds: ReadonlySet<string>;
  readonly askChunkIds: ReadonlySet<string>;
}

/**
 * Precedence mirrors the runner's own lease-state fold (`derive_lease_state`)
 * extended with the chunk-level human facts: an open takeover outranks
 * everything (a human is in the session *now*), then an escalation (blocked on
 * a human), then an open ask (waiting on one), then the lease's own state
 * ({@link deriveLeaseStatus}, the kernel's lease fold).
 */
export function deriveMachineChunkStatus(lease: runnerApi.LeaseView, facts: MachineChunkFacts): MachineChunkStatus {
  const chunkId = lease.chunk_id;
  if (facts.takeoverChunkIds.has(chunkId)) return { label: 'HUMAN IN SESSION', tone: 'takeover' };
  if (facts.escalatedChunkIds.has(chunkId)) return { label: 'NEEDS HUMAN', tone: 'needs' };
  if (facts.askChunkIds.has(chunkId)) return { label: 'WAITING · ASK', tone: 'waiting' };
  return deriveLeaseStatus(lease);
}

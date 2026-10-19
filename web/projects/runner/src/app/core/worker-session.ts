import { type ChunkStepSession, nodeStepKey, type runnerApi } from 'fleet';

/** Whether `lease` is a live worker — not yet exited or closed. A chunk has at most one. */
export function isLiveLease(lease: runnerApi.LeaseView): boolean {
  return lease.state !== 'exited' && lease.state !== 'closed';
}

/** How a closed lease's closure reads to an operator, by `closure_reason`. */
const ENDINGS: Readonly<Record<string, string>> = {
  transitioned: 'completed',
  reaped: 'timed out',
  failed: 'failed',
  escalated: 'escalated',
  parked: 'parked',
  released: 'released',
  preempted: 'restarted',
};

/**
 * How `lease`'s session ended, in operator wording — `null` while it is live, `exited` once
 * its worker exited without a closure yet, else its closure reason (an unknown reason
 * shows as itself).
 */
export function sessionEnding(lease: runnerApi.LeaseView): string | null {
  if (lease.state === 'exited') return 'exited';
  if (lease.state !== 'closed') return null;
  const reason = lease.closure_reason ?? 'unknown';
  return ENDINGS[reason] ?? reason;
}

/** One chunk's leases as the Node history's step sessions, keyed by `(node_id, epoch)`. */
export function stepSessions(leases: readonly runnerApi.LeaseView[]): ReadonlyMap<string, ChunkStepSession> {
  return new Map(
    leases.map((lease) => [
      nodeStepKey(lease.node_id, lease.epoch),
      {
        environment: lease.environment_id,
        pid: lease.pid,
        startedAt: lease.created_at,
        endedAt: lease.closed_at,
        ending: sessionEnding(lease),
      },
    ]),
  );
}

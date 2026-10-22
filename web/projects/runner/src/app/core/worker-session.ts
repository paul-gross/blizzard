import { type ChunkStepSession, nodeStepKey, openLookup, type runnerApi } from 'fleet';

/** Whether `lease` is a live worker — not yet exited or closed. A chunk has at most one. */
export function isLiveLease(lease: runnerApi.LeaseView): boolean {
  return lease.state !== 'exited' && lease.state !== 'closed';
}

/** How a closed lease's closure reads to an operator, by `closure_reason` — every published reason, so a new one
 * fails to compile until it is worded; a reason this build does not know shows as itself. */
const ENDINGS = openLookup<runnerApi.LeaseClosureReason, string>(
  {
    transitioned: 'completed',
    reaped: 'timed out',
    failed: 'failed',
    escalated: 'escalated',
    parked: 'parked',
    released: 'released',
    preempted: 'restarted',
  },
  (raw) => raw,
);

/**
 * How `lease`'s session ended, in operator wording — `null` while it is live, `exited` once
 * its worker exited without a closure yet, else its closure reason (an unknown reason
 * shows as itself).
 */
export function sessionEnding(lease: runnerApi.LeaseView): string | null {
  if (lease.state === 'exited') return 'exited';
  if (lease.state !== 'closed') return null;
  const reason = lease.closure_reason ?? 'unknown';
  return ENDINGS(reason);
}

/**
 * One chunk's leases as the Node history's step sessions, keyed by `(node_id, epoch)`. The leases may come off several
 * runners — a chunk that moved keeps its earlier steps on the runner that ran them — and the first lease per key wins,
 * so a list ordered freshest first (`mergeLeases`) keeps each step's freshest attempt.
 */
export function stepSessions(leases: readonly runnerApi.LeaseView[]): ReadonlyMap<string, ChunkStepSession> {
  const sessions = new Map<string, ChunkStepSession>();
  for (const lease of leases) {
    const key = nodeStepKey(lease.node_id, lease.epoch);
    if (!sessions.has(key)) sessions.set(key, session(lease));
  }
  return sessions;
}

function session(lease: runnerApi.LeaseView): ChunkStepSession {
  return {
    environment: lease.environment_id,
    pid: lease.pid,
    startedAt: lease.created_at,
    endedAt: lease.closed_at,
    ending: sessionEnding(lease),
  };
}

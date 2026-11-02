import { openLookup, type runnerApi } from 'fleet';

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


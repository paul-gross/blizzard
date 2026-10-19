import { ageMs, compactRef, type DaemonTarget, formatAge, runnerClient, type runnerApi } from 'fleet';

import type { PanelLease } from '../../core/panel-reads.model';
import { isLiveLease } from '../../core/worker-session';

/** The detail read's target — the runner `runnerId` names, or `null` while no chunk is selected. */
export function runnerTarget(runnerId: string | null): DaemonTarget | null {
  return runnerId === null ? null : { plane: 'runner', client: runnerClient, runnerId };
}

/** The chunk's live worker — its newest lease while that has not ended, else `null`. */
export function liveWorker(newest: PanelLease | null): PanelLease | null {
  return newest && isLiveLease(newest) ? newest : null;
}

/** The lease's compact ref, or blank when nothing is selected. */
export function leaseRefLabel(lease: runnerApi.LeaseView | null): string {
  return lease ? compactRef(lease.lease_id) : '';
}

/**
 * `-34s` shorthand, or `—` before the first beat / past the skew bound / once the
 * lease is closed — decoration only; the server-derived state carries liveness
 * (`bzh:utc-instants`).
 */
export function heartbeatLabel(lease: runnerApi.LeaseView | null, now: number): string {
  if (!lease || lease.state === 'closed') return '—';
  const age = ageMs(lease.last_heartbeat_at, now);
  return age === null ? '—' : formatAge(age);
}

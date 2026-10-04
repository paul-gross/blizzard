import { ageMs, compactRef, formatAge, type runnerApi } from 'fleet';

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

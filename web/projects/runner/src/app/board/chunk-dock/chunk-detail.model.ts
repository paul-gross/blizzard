import { ageMs, compactRef, formatAge, runnerDisplayName, type runnerApi } from 'fleet';

/** This runner's name for the header's `pauseCopy`/`resumeCopy` `<runner>` slot: its display name once
 * registered, its configured name alone before its first registration gives it an id, `null` before
 * the dashboard read resolves. */
export function ownRunnerLabel(
  runner: { readonly runner_id: string | null; readonly runner_name?: string | null } | null | undefined,
): string | null {
  if (!runner) return null;
  return runner.runner_id ? runnerDisplayName(runner.runner_id, runner.runner_name) : (runner.runner_name ?? null);
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

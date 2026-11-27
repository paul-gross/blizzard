import type { UnknownHostValue } from 'fleet/core';

export type KnownSelftestStatus = 'running' | 'passed' | 'failed';
/** A selftest run's status; `UnknownHostValue` carries one this build cannot name. */
export type SelftestStatus = KnownSelftestStatus | UnknownHostValue;

/** One check's pass/fail result. */
export interface SelftestCheck {
  readonly name: string;
  readonly passed: boolean;
  readonly detail: string;
}

/** A selftest run's current state. `settled` is the runner's own judgment that the run no longer moves,
 * `null` from a runner that does not say. */
export interface SelftestRun {
  readonly id: string;
  readonly harness: string;
  readonly status: SelftestStatus;
  readonly settled: boolean | null;
  readonly checks: readonly SelftestCheck[];
  readonly error: string | null;
}

/** A run's status as text — an unknown one as the runner sent it. */
export function selftestStatusLabel(status: SelftestStatus): string {
  return typeof status === 'string' ? status : status.raw;
}

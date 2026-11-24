import type { SelftestCheck, SelftestRun } from './selftest.domain';

/** One passed check. */
export function selftestCheck(overrides: Partial<SelftestCheck> = {}): SelftestCheck {
  return { name: 'spawn', passed: true, detail: '', ...overrides };
}

/** A selftest run of `mock`, still running. */
export function selftestRun(overrides: Partial<SelftestRun> = {}): SelftestRun {
  return { id: 'st_1', harness: 'mock', status: 'running', settled: false, checks: [], error: null, ...overrides };
}

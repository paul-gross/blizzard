import type { UnknownHostValue } from 'fleet';
import type { runnerApi } from 'fleet/runner-shell';
import { openLookup } from 'fleet/shell';

import type { SelftestRun, SelftestStatus } from './selftest.domain';

const selftestStatuses = openLookup<runnerApi.SelfTestStatus, SelftestStatus>(
  { running: 'running', passed: 'passed', failed: 'failed' },
  (raw): UnknownHostValue => ({ kind: 'unknown', raw }),
);

/** A selftest run, its `settled` carried from the field (`null` when the runner sends none). */
export function fromSelfTestView(wire: runnerApi.SelfTestView): SelftestRun {
  return {
    id: wire.id,
    harness: wire.harness,
    status: selftestStatuses(wire.status),
    settled: wire.settled ?? null,
    checks: wire.checks.map(({ name, passed, detail }) => ({ name, passed, detail })),
    error: wire.error,
  };
}

import type { runnerApi } from 'fleet/runner-shell';

import { fromSelfTestView } from './selftest.translate';

const wire = (overrides: Partial<runnerApi.SelfTestView> = {}): runnerApi.SelfTestView => ({
  id: 'st_1', harness: 'mock', status: 'passed', settled: true, error: null,
  checks: [{ name: 'spawn', passed: true, detail: 'spawned' }], ...overrides,
});

describe('fromSelfTestView', () => {
  it('carries the run, its checks and its settled field', () => {
    expect(fromSelfTestView(wire())).toEqual({
      id: 'st_1', harness: 'mock', status: 'passed', settled: true, error: null,
      checks: [{ name: 'spawn', passed: true, detail: 'spawned' }],
    });
  });

  it('closes an unforeseen status as unknown', () => {
    expect(fromSelfTestView(wire({ status: 'queued' as runnerApi.SelfTestStatus })).status).toEqual({ kind: 'unknown', raw: 'queued' });
  });

  it('reads a run without settled as unclassified', () => {
    const bare: Partial<runnerApi.SelfTestView> = wire();
    delete bare.settled;
    expect(fromSelfTestView(bare as runnerApi.SelfTestView).settled).toBeNull();
  });
});

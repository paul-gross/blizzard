import { describe, expect, it } from 'vitest';
import { hostRunnerView, pauseStateView } from 'fleet/testing';

import { fromHostRunnerView } from './host-runner.translate';

describe('fromHostRunnerView', () => {
  it('carries the fields the shell reads, its brakes among them', () => {
    expect(
      fromHostRunnerView(
        hostRunnerView({ runner_id: 'rn_2', name: 'r2', state: 'halted', served: false, agents_in_use: 1, max_agents: 3, pause: pauseStateView({ hub: true }) }),
      ),
    ).toEqual({
      runner_id: 'rn_2',
      name: 'r2',
      state: 'halted',
      served: false,
      agents_in_use: 1,
      max_agents: 3,
      pause: { local: false, hub: true, effective: false, local_reason: null },
      bindings: [],
      halted_reasons: [],
      start_failure: null,
      throttle: null,
    });
  });

  it('reads a state this build cannot name as unknown', () => {
    expect(fromHostRunnerView(hostRunnerView({ state: 'draining' as never })).state).toBe('unknown');
  });

  it('reads an absent pause as null', () => {
    expect(fromHostRunnerView(hostRunnerView({ pause: null })).pause).toBeNull();
  });

  it('carries served apart from state — a halted runner whose routes still answer', () => {
    const runner = fromHostRunnerView(hostRunnerView({ state: 'halted', served: true, halted_reasons: ['hub refused its version'] }));
    expect(runner.state).toBe('halted');
    expect(runner.served).toBe(true);
    expect(runner.halted_reasons).toEqual(['hub refused its version']);
  });

  it('carries its bindings, start failure and throttle', () => {
    const runner = fromHostRunnerView(
      hostRunnerView({
        state: 'start_failed',
        bindings: [{ project: 'blizzard', workspace_id: 'ws', agents: 2 }],
        start_failure: { step: 'compose', error: 'boom' },
      }),
    );
    expect(runner.bindings).toEqual([{ project: 'blizzard', workspace_id: 'ws', agents: 2 }]);
    expect(runner.start_failure).toEqual({ step: 'compose', error: 'boom' });
    expect(runner.throttle).toBeNull();
  });

  it('reads a start step this build cannot name as unknown, raw', () => {
    const runner = fromHostRunnerView(hostRunnerView({ start_failure: { step: 'warmup' as never, error: 'boom' } }));
    expect(runner.start_failure?.step).toEqual({ kind: 'unknown', raw: 'warmup' });
  });
});

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
      pause: { local: false, hub: true, local_reason: null },
    });
  });

  it('reads a state this build cannot name as unknown', () => {
    expect(fromHostRunnerView(hostRunnerView({ state: 'draining' as never })).state).toBe('unknown');
  });

  it('reads an absent pause as null', () => {
    expect(fromHostRunnerView(hostRunnerView({ pause: null })).pause).toBeNull();
  });
});

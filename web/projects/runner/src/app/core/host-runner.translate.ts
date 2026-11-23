import type { runnerApi } from 'fleet/runner-shell';
import { openLookup } from 'fleet/shell';

import type { HostRunner, HostRunnerState } from './host-runner.domain';
import { fromPauseStateView } from './pause-state.translate';

const stateOf = openLookup<runnerApi.HostRunnerView['state'], HostRunnerState>(
  { serving: 'serving', halted: 'halted', starting: 'starting', start_failed: 'start_failed', retired: 'retired' },
  () => 'unknown',
);

export function fromHostRunnerView(wire: runnerApi.HostRunnerView): HostRunner {
  return {
    runner_id: wire.runner_id,
    name: wire.name,
    state: stateOf(wire.state),
    served: wire.served,
    agents_in_use: wire.agents_in_use,
    max_agents: wire.max_agents,
    pause: fromPauseStateView(wire.pause),
  };
}

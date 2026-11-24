import type { runnerApi } from 'fleet/runner-shell';
import { openLookup } from 'fleet/shell';

import type { HostRunner, HostRunnerState, StartStep } from './host-runner.domain';
import { fromPauseStateView } from './pause-state.translate';
import { fromThrottleStatusView } from './throttle.translate';

const stateOf = openLookup<runnerApi.HostRunnerView['state'], HostRunnerState>(
  { serving: 'serving', halted: 'halted', starting: 'starting', start_failed: 'start_failed', retired: 'retired' },
  () => 'unknown',
);

const stepOf = openLookup<NonNullable<runnerApi.HostRunnerView['start_failure']>['step'], StartStep>(
  {
    seed: 'seed',
    compose: 'compose',
    worker_shell: 'worker_shell',
    tracing: 'tracing',
    worker_scratch: 'worker_scratch',
    resume: 'resume',
    takeovers: 'takeovers',
    sessions: 'sessions',
    serve: 'serve',
  },
  (raw) => ({ kind: 'unknown', raw }),
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
    bindings: (wire.bindings ?? []).map(({ project, workspace_id, agents }) => ({ project, workspace_id, agents })),
    halted_reasons: [...(wire.halted_reasons ?? [])],
    start_failure: wire.start_failure ? { step: stepOf(wire.start_failure.step), error: wire.start_failure.error } : null,
    throttle: fromThrottleStatusView(wire.throttle),
  };
}

import type { HostRunner } from './host-runner.domain';

export function hostRunner(overrides: Partial<HostRunner> = {}): HostRunner {
  return {
    runner_id: 'rn_1',
    name: 'r1',
    state: 'serving',
    served: true,
    agents_in_use: 0,
    max_agents: 0,
    pause: null,
    ...overrides,
  };
}

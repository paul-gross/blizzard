import type { runnerApi } from 'fleet/runner-shell';

import type { HostEnvironment, WorkspaceEnvironments } from './host-environment.domain';

export function fromHostEnvironmentView(wire: runnerApi.HostEnvironmentView): HostEnvironment {
  return { held: wire.held, runner_id: wire.runner_id };
}

export function fromHostWorkspaceEnvironmentsView(wire: runnerApi.HostWorkspaceEnvironmentsView): WorkspaceEnvironments {
  return { items: wire.items.map(fromHostEnvironmentView) };
}

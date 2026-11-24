import type { runnerApi } from 'fleet/runner-shell';

import type { HostEnvironment, WorkspaceEnvironments } from './host-environment.domain';

export function fromHostEnvironmentView(wire: runnerApi.HostEnvironmentView): HostEnvironment {
  return {
    environment_id: wire.environment_id,
    held: wire.held,
    chunk_id: wire.chunk_id ?? null,
    project: wire.project ?? null,
    runner_id: wire.runner_id ?? null,
  };
}

export function fromHostWorkspaceEnvironmentsView(wire: runnerApi.HostWorkspaceEnvironmentsView): WorkspaceEnvironments {
  return { workspace_id: wire.workspace_id, items: wire.items.map(fromHostEnvironmentView) };
}

import type { HostEnvironment, WorkspaceEnvironments } from './host-environment.domain';

export function hostEnvironment(overrides: Partial<HostEnvironment> = {}): HostEnvironment {
  return { environment_id: 'e1', held: false, chunk_id: null, project: null, runner_id: null, ...overrides };
}

export function workspaceEnvironments(items: readonly HostEnvironment[], workspaceId = 'ws'): WorkspaceEnvironments {
  return { workspace_id: workspaceId, items };
}

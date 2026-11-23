import type { HostEnvironment, WorkspaceEnvironments } from './host-environment.domain';

export function hostEnvironment(overrides: Partial<HostEnvironment> = {}): HostEnvironment {
  return { held: false, runner_id: null, ...overrides };
}

export function workspaceEnvironments(items: readonly HostEnvironment[]): WorkspaceEnvironments {
  return { items };
}

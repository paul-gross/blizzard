import type { HostWorkspace } from './host-workspace.domain';

export function hostWorkspace(overrides: Partial<HostWorkspace> = {}): HostWorkspace {
  return { workspace_id: 'ws', max_environments: 1, ...overrides };
}

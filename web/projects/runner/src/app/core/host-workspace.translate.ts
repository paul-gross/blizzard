import type { runnerApi } from 'fleet/runner-shell';

import { hostRecordFacts } from './host-record.translate';
import type { HostWorkspace } from './host-workspace.domain';

export function fromHostWorkspaceView(wire: runnerApi.HostWorkspaceView): HostWorkspace {
  return { workspace_id: wire.workspace_id, max_environments: wire.max_environments, ...hostRecordFacts(wire) };
}

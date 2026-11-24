import type { RunnerClaimChunk } from './runner-claims.domain';

/** One board chunk held by no runner, running at no node, at whatever id the caller names. */
export function runnerClaimChunk(overrides: Pick<RunnerClaimChunk, 'chunk_id'> & Partial<RunnerClaimChunk>): RunnerClaimChunk {
  return {
    runner_id: null,
    current_node_id: null,
    current_node_name: null,
    laneStatus: { kind: 'running' },
    environment_count: 0,
    workspace_id: null,
    project_id: null,
    project_slug: null,
    ...overrides,
  };
}

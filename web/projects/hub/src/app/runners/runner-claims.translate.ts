import { type ChunkSummary, laneStatus } from 'fleet/shell';
import type { RunnerClaimChunk } from './runner-claims.domain';

export function toRunnerClaimChunk(wire: ChunkSummary): RunnerClaimChunk {
  return {
    chunk_id: wire.chunk_id,
    runner_id: wire.runner_id ?? null,
    current_node_id: wire.current_node_id ?? null,
    current_node_name: wire.current_node_name ?? null,
    laneStatus: laneStatus(wire.status),
    environment_count: wire.environment_count ?? 0,
    workspace_id: wire.workspace_id ?? null,
    project_id: wire.project_id ?? null,
    project_slug: wire.project_slug ?? null,
  };
}

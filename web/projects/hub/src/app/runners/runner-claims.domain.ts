import type { LaneStatus } from 'fleet';

/** A board chunk as the runner registry reads it: who holds it, where it sits, and what it holds. `runner_id` is set
 * only while the chunk is in progress, so a set one *is* a live claim. */
export interface RunnerClaimChunk {
  readonly chunk_id: string;
  readonly runner_id: string | null;
  readonly current_node_id: string | null;
  readonly current_node_name: string | null;
  readonly laneStatus: LaneStatus;
  readonly environment_count: number;
  readonly workspace_id: string | null;
  readonly project_id: string | null;
  readonly project_slug: string | null;
}

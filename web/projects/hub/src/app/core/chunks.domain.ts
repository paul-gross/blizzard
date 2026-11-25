import type { ChunkDetailAggregate, LaneStatus, UrgencyReference } from 'fleet';

/** A node's latest visit in a chunk's graph. Unknown states retain the server's value. */
export type GraphProgressState =
  | { readonly kind: 'current' | 'passed' | 'failed' | 'unvisited' }
  | { readonly kind: 'unknown'; readonly raw: string };

export interface GraphProgressNode {
  readonly nodeId: string;
  readonly name: string;
  readonly state: GraphProgressState;
}

export interface BoardDoneWindow {
  readonly done_since: string;
  readonly floor: number;
  readonly window_seconds: number;
}

/** Metadata for each drained page, kept separately from the raw cached pages. */
export interface BoardPageMetadata {
  readonly next_cursor: string | null;
  readonly board_done_policy: BoardDoneWindow | null | undefined;
}
/** Chunk facts for runner claims and workspace occupancy. */
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

/** Board facts translated from the chunk summary, with server judgments intact. */
export type BoardSummary = RunnerClaimChunk & Pick<ChunkDetailAggregate,
  'awaiting_external_merge' | 'blocked' | 'cost' | 'current_node_id' | 'current_node_name' |
  'flow_places' | 'graph_id' | 'graph_name' | 'landed_repos' | 'open_prs' |
  'project_id' | 'project_name' | 'project_slug' | 'promotable' | 'terminal' | 'work_refs'> & {
  readonly status: string;
  readonly completed_at: string | null;
  readonly runner_name: string | null;
  readonly graphProgress: readonly GraphProgressNode[];
  readonly urgency: UrgencyReference | null;
};

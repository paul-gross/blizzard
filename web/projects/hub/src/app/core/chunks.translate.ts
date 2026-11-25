import type { hubApi } from 'fleet';
import { fromFlowState, unlisted } from 'fleet';
import { laneStatus } from 'fleet/shell';
import type { BoardPageMetadata, BoardSummary, GraphProgressNode, GraphProgressState, RunnerClaimChunk } from './chunks.domain';
import { fromUrgencyRef } from './urgency.translate';

export function fromRunnerClaimChunk(chunk: hubApi.ChunkSummary): RunnerClaimChunk {
  return {
    chunk_id: chunk.chunk_id,
    runner_id: chunk.runner_id,
    current_node_id: chunk.current_node_id,
    current_node_name: chunk.current_node_name,
    laneStatus: laneStatus(chunk.status),
    environment_count: chunk.environment_count,
    workspace_id: chunk.workspace_id,
    project_id: chunk.project_id,
    project_slug: chunk.project_slug,
  };
}

export function fromBoardSummary(chunk: hubApi.ChunkSummary): BoardSummary {
  return {
    ...fromRunnerClaimChunk(chunk),
    status: chunk.status,
    awaiting_external_merge: chunk.awaiting_external_merge,
    blocked: chunk.blocked && { ...chunk.blocked },
    cost: { ...chunk.cost },
    completed_at: chunk.completed_at,
    flow_places: chunk.flow_places.map((place) => ({ ...place, state: fromFlowState(place.state) })),
    graph_id: chunk.graph_id,
    graph_name: chunk.graph_name,
    graphProgress: chunk.graph_progress.map(fromGraphProgressNode),
    landed_repos: chunk.landed_repos.map((repo) => ({ ...repo })),
    open_prs: chunk.open_prs.map((pr) => ({ ...pr })),
    project_name: chunk.project_name,
    promotable: chunk.promotable,
    runner_name: chunk.runner_name,
    terminal: chunk.terminal,
    urgency: chunk.urgency && fromUrgencyRef(chunk.urgency),
    work_refs: chunk.work_refs.map((ref) => ({ ...ref })),
  };
}

/** Read the open graph-progress vocabulary without losing an unrecognized value. */
export function fromGraphProgressState(state: hubApi.GraphProgressState): GraphProgressState {
  switch (state) {
    case 'current':
    case 'passed':
    case 'failed':
    case 'unvisited':
      return { kind: state };
    default:
      return { kind: 'unknown', raw: unlisted(state) };
  }
}

export function fromGraphProgressNode(node: hubApi.GraphProgressNodeView): GraphProgressNode {
  return {
    nodeId: node.node_id,
    name: node.name,
    state: fromGraphProgressState(node.state),
  };
}

export function fromBoardPage(page: hubApi.ChunksPageView): BoardPageMetadata {
  const policy = page.board_done_policy;
  return {
    next_cursor: page.next_cursor,
    board_done_policy: policy == null ? policy : {
      done_since: policy.done_since,
      floor: policy.floor,
      window_seconds: policy.window_seconds,
    },
  };
}

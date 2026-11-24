import type { LaneCounts } from 'fleet';
import { chunkUsageTotalView } from 'fleet/testing';
import type { BoardChunk } from '../core/chunks.model';

export function boardChunk(overrides: Pick<BoardChunk, 'chunk_id' | 'graph_id' | 'status' | 'laneStatus'> & Partial<BoardChunk>): BoardChunk {
  return {
    awaiting_external_merge: false,
    blocked: null,
    bounce_count: 0,
    closed_prs: [],
    completed_at: null,
    cost: chunkUsageTotalView({ input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0, cost_usd: 0 }),
    current_node_id: null,
    current_node_name: null,
    default_effort: null,
    default_harnesses: [],
    default_model: [],
    environment_count: 0,
    flow_places: [],
    graph_name: null,
    graph_progress: [],
    graphProgress: [],
    landed: false,
    landed_repos: [],
    latest_bounce: null,
    open_prs: [],
    project_id: null,
    project_name: null,
    project_slug: null,
    promotable: false,
    runner_id: null,
    runner_name: null,
    workspace_id: null,
    terminal: false,
    urgency: null,
    work_refs: [],
    ...overrides,
  };
}

export function boardCounts(statuses: Partial<LaneCounts['statuses']> = {}, terminal = 12, total = 23): LaneCounts {
  return { statuses: { not_ready: 2, ready: 3, running: 4, delivering: 1, needs_human: 1, waiting_on_human: 0, paused: 0, stopped: 0, done: 12, ...statuses }, terminal, total };
}

import type { ChunkCountsView, ChunkSummary, FleetSpendView } from '../../api/hub';
import { chunkUsageTotalView } from './chunk';
import { fixtureInstant } from './clock';

/** The board's chunk counts: a fleet mid-day, `total` summing the rest. */
export function chunkCountsView(overrides: Partial<ChunkCountsView> = {}): ChunkCountsView {
  return {
    not_ready: 2,
    ready: 3,
    running: 4,
    delivering: 1,
    needs_human: 1,
    waiting_on_human: 0,
    paused: 0,
    stopped: 0,
    done: 12,
    terminal: 12,
    total: 23,
    done_since: null,
    ...overrides,
  };
}

/** The fleet's spend over the day ending at {@link FIXTURE_NOW}. */
export function fleetSpendView(overrides: Partial<FleetSpendView> = {}): FleetSpendView {
  return {
    since: fixtureInstant(-24 * 3600),
    until: fixtureInstant(0),
    input_tokens: 310_000,
    output_tokens: 120_000,
    cache_read_tokens: 9_400_000,
    cache_create_tokens: 640_000,
    cost_usd: 27.5,
    cost_partial: false,
    estimated_cost_usd: null,
    ...overrides,
  };
}

/** One fleet-list row, unrouted with nothing spent, at whatever id, graph and status the caller names. */
export function chunkSummary(
  overrides: Pick<ChunkSummary, 'chunk_id' | 'graph_id' | 'status'> & Partial<ChunkSummary>,
): ChunkSummary {
  return {
    awaiting_external_merge: false,
    blocked: null,
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
    landed: false,
    landed_repos: [],
    open_prs: [],
    project_id: null,
    project_name: null,
    project_slug: null,
    promotable: false,
    runner_id: null,
    runner_name: null,
    workspace_id: null,
    terminal: false,
    work_refs: [],
    ...overrides,
  };
}

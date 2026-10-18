import type {
  ChunkDetail,
  ChunkEscalationView,
  ChunkNeighborView,
  ChunkUsageTotalView,
  LandedRepoView,
  PrView,
  RouteView,
  TransitionView,
  WorkRefView,
} from '../../api/hub';
import { fixtureInstant } from './clock';

/** A work item as the hub resolves it from its source. */
export function workRefView(overrides: Partial<WorkRefView> = {}): WorkRefView {
  return {
    source: 'github',
    ref: '1274',
    label: 'blizzard#1274',
    web_url: 'https://github.com/paul-gross/blizzard/issues/1274',
    ...overrides,
  };
}

/** One step of a chunk's walk through its graph, recorded a minute before {@link FIXTURE_NOW}. */
export function transitionView(overrides: Partial<TransitionView> = {}): TransitionView {
  return {
    from_node_id: null,
    from_node_name: null,
    to_node_id: 'nd_build',
    to_node_name: 'build',
    choice_name: null,
    epoch: 1,
    graph_id: 'gr_build',
    graph_name: 'build',
    recorded_at: fixtureInstant(-60),
    ...overrides,
  };
}

/** The escalation a `needs_human` chunk carries. */
export function chunkEscalationView(overrides: Partial<ChunkEscalationView> = {}): ChunkEscalationView {
  return {
    epoch: 1,
    cause: 'worker_stuck',
    detail: 'The worker asked for help and was not answered.',
    takeover_command: 'cd /work/ch_01fixture0000000000000000000 && claude --resume se_01fixture',
    wrapped_takeover_command: 'ssh xps -t "cd /work/ch_01fixture0000000000000000000 && claude --resume se_01fixture"',
    ...overrides,
  };
}

/** A prerequisite or dependent of a chunk. */
export function chunkNeighborView(overrides: Partial<ChunkNeighborView> = {}): ChunkNeighborView {
  return {
    chunk_id: 'ch_01neighbor00000000000000000',
    satisfied: true,
    status: 'done',
    ...overrides,
  };
}

/** Where a chunk's work is routed. */
export function routeView(overrides: Partial<RouteView> = {}): RouteView {
  return {
    runner_id: 'rn_01fixture000000000000000000',
    runner_name: 'r-claude',
    workspace_id: 'ws_01fixture000000000000000000',
    environment_ids: ['r-claude-1'],
    ...overrides,
  };
}

/** A pull request on a repo. */
export function prView(overrides: Partial<PrView> = {}): PrView {
  return {
    repo: 'paul-gross/blizzard',
    number: 1301,
    url: 'https://github.com/paul-gross/blizzard/pull/1301',
    ...overrides,
  };
}

/** A repo's work, landed on its base branch. */
export function landedRepoView(overrides: Partial<LandedRepoView> = {}): LandedRepoView {
  return {
    repo: 'paul-gross/blizzard',
    commit_hash: '9f2c4e1a7b3d5860fedcba9876543210abcdef12',
    url: 'https://github.com/paul-gross/blizzard/commit/9f2c4e1a7b3d5860fedcba9876543210abcdef12',
    ...overrides,
  };
}

/** A chunk's spend, summed across its sessions. */
export function chunkUsageTotalView(overrides: Partial<ChunkUsageTotalView> = {}): ChunkUsageTotalView {
  return {
    input_tokens: 42_000,
    output_tokens: 18_500,
    cache_read_tokens: 1_250_000,
    cache_create_tokens: 96_000,
    cost_usd: 3.42,
    cost_partial: false,
    ...overrides,
  };
}

/** One ordinary running chunk, mid-way through its build node. */
export function chunkDetail(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return {
    chunk_id: 'ch_01fixture0000000000000000000',
    graph_id: 'gr_build',
    graph_name: 'build',
    status: 'running',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    latest_epoch: 1,
    work_refs: [workRefView()],
    history: [transitionView()],
    artifacts: [],
    ...overrides,
  };
}

import type {
  BounceView,
  ChunkDetail,
  ChunkEscalationView,
  ChunkNeighborhoodView,
  ChunkNeighborView,
  ChunkUsageTotalView,
  DecisionView,
  ChunkUsageView,
  LandedRepoView,
  PrView,
  QuestionView,
  RouteView,
  TransitionView,
  WorkItemEntry,
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
    billed_partial: false,
    estimated_cost_usd: null,
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
    awaiting_external_merge: false,
    blocked: null,
    bounces: [],
    closed_prs: [],
    completable: false,
    cost: chunkUsageTotalView(),
    current_node_terminal: false,
    decision: null,
    default_effort: null,
    default_harnesses: [],
    default_model: [],
    deletable: false,
    delete_refusal: null,
    escalation: null,
    graph_created_at: null,
    graph_editable: false,
    intended_migration: null,
    landed: false,
    landed_repos: [],
    migrations: [],
    neighborhood: chunkNeighborhoodView(),
    open_prs: [],
    pausable: false,
    pause: null,
    pending: null,
    project_id: null,
    project_name: null,
    project_slug: null,
    promotable: false,
    questions: [],
    restarts: [],
    route: null,
    status_if_paused: null,
    terminal: false,
    usage: [],
    ...overrides,
  };
}

/** An open question the worker asked, with options to pick from, asked a minute before {@link FIXTURE_NOW}. */
export function questionView(overrides: Partial<QuestionView> = {}): QuestionView {
  return {
    question_id: 'qn_01fixture0000000000000000000',
    chunk_id: 'ch_01fixture0000000000000000000',
    runner_id: 'rn_01fixture000000000000000000',
    epoch: 1,
    question: 'Should the stories reach the shared builders, or keep their own fixtures?',
    options: ['Shared builders', 'Story-local fixtures'],
    answered: false,
    asked_at: fixtureInstant(-60),
    answer: null,
    answered_at: null,
    answered_by: null,
    delivered: false,
    delivered_at: null,
    harness_id: null,
    node_id: null,
    project_id: null,
    project_name: null,
    project_slug: null,
    runner_name: null,
    session_id: null,
    ...overrides,
  };
}

/** An unresolved gate decision with two choices and one red check, submitted a minute before {@link FIXTURE_NOW}. */
export function decisionView(overrides: Partial<DecisionView> = {}): DecisionView {
  return {
    decision_id: 'dc_01fixture0000000000000000000',
    chunk_id: 'ch_01fixture0000000000000000000',
    node_id: 'nd_review',
    node_name: 'review',
    epoch: 1,
    choices: [
      { name: 'approve', description: 'Accept the change and move on.', requires_checks: true, would_override_red_checks: true },
      { name: 'rework', description: 'Send the change back to build.', requires_checks: false, would_override_red_checks: false },
    ],
    check_results: [
      { command: 'npm run lint', passed: true },
      { command: 'npm run test', passed: false },
    ],
    submitted_at: fixtureInstant(-60),
    imposed_by_runner_id: null,
    imposed_by_runner_name: null,
    overrode_red_checks: false,
    project_id: null,
    project_name: null,
    project_slug: null,
    resolved_at: null,
    resolved_by: null,
    resolved_choice: null,
    transitioned: false,
    ...overrides,
  };
}

/** A work item as its source returns it, fetched at {@link FIXTURE_NOW}. */
export function workItemEntry(overrides: Partial<WorkItemEntry> = {}): WorkItemEntry {
  return {
    source: 'github',
    ref: '1276',
    label: 'blizzard#1276',
    title: 'Story the chunk page tabs and transcripts',
    body: 'Give the chunk page tabs and the transcript pieces a stories file each.',
    web_url: 'https://github.com/paul-gross/blizzard/issues/1276',
    fetched_at: fixtureInstant(0),
    author: null,
    comments: [],
    error: null,
    hub_source: false,
    slug: null,
    stated_priority: null,
    ...overrides,
  };
}

/** A refusal the hub recorded against a worker's result, a minute before {@link FIXTURE_NOW}. */
export function bounceView(overrides: Partial<BounceView> = {}): BounceView {
  return {
    cause: 'missing_artifact',
    envelope: JSON.stringify({ cause: 'missing_artifact', detail: 'The step ended without declaring `commit`.' }),
    recorded_at: fixtureInstant(-45),
    ...overrides,
  };
}

/** One invocation's spend at a node and epoch. */
export function chunkUsageView(overrides: Partial<ChunkUsageView> = {}): ChunkUsageView {
  return {
    node_id: 'nd_build',
    epoch: 1,
    kind: 'spawn',
    model: 'claude-sonnet-5-5',
    input_tokens: 12_000,
    output_tokens: 4_800,
    cache_read_tokens: 310_000,
    cache_create_tokens: 24_000,
    cost_usd: 0.86,
    cost_partial: false,
    estimated_cost_usd: null,
    harness_id: null,
    harness_version: null,
    ...overrides,
  };
}

/** The chunks a chunk waits on and the chunks that wait on it. */
export function chunkNeighborhoodView(overrides: Partial<ChunkNeighborhoodView> = {}): ChunkNeighborhoodView {
  return { prerequisites: [], dependents: [], ...overrides };
}

/** A chunk accepted but not yet claimed: ready, with no history. */
export function queuedChunkDetail(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return chunkDetail({
    status: 'ready',
    current_node_id: null,
    current_node_name: null,
    latest_epoch: null,
    history: [],
    ...overrides,
  });
}

/** A chunk on its third build epoch, with spend recorded for the epochs behind it. */
export function midFlightChunkDetail(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return chunkDetail({
    latest_epoch: 3,
    history: [
      transitionView({ to_node_id: 'nd_plan', to_node_name: 'plan', epoch: 1, recorded_at: fixtureInstant(-900) }),
      transitionView({
        from_node_id: 'nd_plan',
        from_node_name: 'plan',
        choice_name: 'pass',
        epoch: 2,
        recorded_at: fixtureInstant(-600),
      }),
      transitionView({
        from_node_id: 'nd_build',
        from_node_name: 'build',
        choice_name: 'pass',
        epoch: 3,
        recorded_at: fixtureInstant(-60),
      }),
    ],
    usage: [
      chunkUsageView({ node_id: 'nd_plan', epoch: 1 }),
      chunkUsageView({ node_id: 'nd_build', epoch: 2, cost_usd: 1.24 }),
    ],
    ...overrides,
  });
}

/** A chunk whose review failed and routed it back to build, with the hub's recorded bounce beside it. */
export function bouncedChunkDetail(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return chunkDetail({
    latest_epoch: 2,
    history: [
      transitionView({ to_node_id: 'nd_build', to_node_name: 'build', epoch: 1, recorded_at: fixtureInstant(-600) }),
      transitionView({
        from_node_id: 'nd_build',
        from_node_name: 'build',
        to_node_id: 'nd_review',
        to_node_name: 'review',
        choice_name: 'pass',
        epoch: 1,
        recorded_at: fixtureInstant(-300),
      }),
      transitionView({
        from_node_id: 'nd_review',
        from_node_name: 'review',
        choice_name: 'fail',
        epoch: 2,
        recorded_at: fixtureInstant(-60),
      }),
    ],
    bounces: [bounceView({ recorded_at: fixtureInstant(-50) })],
    usage: [chunkUsageView(), chunkUsageView({ node_id: 'nd_review', cost_usd: 0.31 })],
    ...overrides,
  });
}

/** A chunk stuck with a worker that asked for a human. */
export function escalatedChunkDetail(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return chunkDetail({
    status: 'needs_human',
    escalation: chunkEscalationView(),
    ...overrides,
  });
}

/** A chunk delivered to its base branch. */
export function landedChunkDetail(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return chunkDetail({
    status: 'done',
    terminal: true,
    landed: true,
    current_node_id: 'nd_done',
    current_node_name: 'done',
    current_node_terminal: true,
    landed_repos: [landedRepoView()],
    cost: chunkUsageTotalView(),
    history: [
      transitionView({ recorded_at: fixtureInstant(-600) }),
      transitionView({
        from_node_id: 'nd_build',
        from_node_name: 'build',
        to_node_id: 'nd_done',
        to_node_name: 'done',
        choice_name: 'pass',
        epoch: 1,
        recorded_at: fixtureInstant(-60),
      }),
    ],
    ...overrides,
  });
}

/** A chunk an operator stopped mid-walk. */
export function stoppedChunkDetail(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return chunkDetail({ status: 'stopped', terminal: true, ...overrides });
}

import type { ChunkArtifactStore } from '../../chunk/chunk-detail/artifact.model';
import type { WorkItem } from '../../chunk/chunk-detail/work-item.model';
import type { ChunkEscalationState } from '../../chunk/chunk-detail/escalation.model';
import type { ChunkDetail, ChunkDecision, ChunkQuestion, ChunkTransition, ChunkUsage, ChunkUsageTotal, ChunkPr, ChunkLandedRepo, ChunkRoute, ChunkWorkRef } from '../../chunk/chunk-page/chunk-detail.model';
import { awaitingHuman } from '../../chunk/awaiting-human/awaiting-human.domain';
import { fixtureInstant } from './clock';

/** A chunk's artifact store for presentational components. */
export function chunkArtifactStore(overrides: Partial<ChunkArtifactStore> = {}): ChunkArtifactStore {
  return { chunk_id: 'ch_fixture', artifacts: [], ...overrides };
}

/** A resolved work item for presentation tests. */
export function workItem(overrides: Partial<WorkItem> = {}): WorkItem {
  return {
    source: 'github', ref: '1276', label: 'blizzard#1276', title: 'Story the chunk page tabs and transcripts',
    body: 'Give the chunk page tabs and the transcript pieces a stories file each.',
    web_url: 'https://github.com/paul-gross/blizzard/issues/1276', fetched_at: '2026-07-15T00:00:00Z',
    author: null, comments: [], error: null, hub_source: false, slug: null, stated_priority: null,
    ...overrides,
  };
}

export function chunkEscalation(overrides: Partial<ChunkEscalationState> = {}): ChunkEscalationState {
  return {
    epoch: 1,
    cause: 'worker_stuck',
    detail: 'The worker asked for help and was not answered.',
    takeover_command: 'cd /work/ch_fixture && claude --resume se_fixture',
    wrapped_takeover_command: 'blizzard runner takeover ch_fixture',
    ...overrides,
  };
}

export function domainTransition(overrides: Partial<ChunkTransition> = {}): ChunkTransition {
  return { from_node_id: null, from_node_name: null, to_node_id: 'nd_build', to_node_name: 'build', choice_name: null,
    epoch: 1, graph_id: 'gr_build', graph_name: 'build', recorded_at: '2026-07-15T00:00:00Z', ...overrides };
}

export function domainPr(overrides: Partial<ChunkPr> = {}): ChunkPr {
  return { repo: 'paul-gross/blizzard', number: 1301, url: 'https://github.com/paul-gross/blizzard/pull/1301', ...overrides };
}

export function domainLandedRepo(overrides: Partial<ChunkLandedRepo> = {}): ChunkLandedRepo {
  return { repo: 'paul-gross/blizzard', commit_hash: '9f2c4e1a7b3d5860fedcba9876543210abcdef12',
    url: 'https://github.com/paul-gross/blizzard/commit/9f2c4e1a7b3d5860fedcba9876543210abcdef12', ...overrides };
}

export function domainRoute(overrides: Partial<ChunkRoute> = {}): ChunkRoute {
  return { runner_id: 'rn_01fixture000000000000000000', runner_name: 'r-claude',
    workspace_id: 'ws_01fixture000000000000000000', environment_ids: ['r-claude-1'], ...overrides };
}

export function domainWorkRef(overrides: Partial<ChunkWorkRef> = {}): ChunkWorkRef {
  return { source: 'github', ref: '1274', label: 'blizzard#1274', web_url: 'https://github.com/paul-gross/blizzard/issues/1274', ...overrides };
}

export function domainBounce(overrides: Partial<ChunkDetail['bounces'][number]> = {}): ChunkDetail['bounces'][number] {
  return { cause: 'missing_artifact', envelope: JSON.stringify({ cause: 'missing_artifact', detail: 'The step ended without declaring `commit`.' }),
    recorded_at: fixtureInstant(-45), withdrawn: false, rework: false, ...overrides };
}

export function domainUsage(overrides: Partial<ChunkUsage> = {}): ChunkUsage {
  return { node_id: 'nd_build', epoch: 1, kind: { kind: 'spawn' }, model: 'claude-sonnet-5-5', input_tokens: 12_000, output_tokens: 4_800,
    cache_read_tokens: 310_000, cache_create_tokens: 24_000, cost_usd: 0.86, cost_partial: false, estimated_cost_usd: null,
    harness_id: null, harness_version: null, runner_id: null, ...overrides };
}

export function domainUsageTotal(overrides: Partial<ChunkUsageTotal> = {}): ChunkUsageTotal {
  return { input_tokens: 42_000, output_tokens: 18_500, cache_read_tokens: 1_250_000, cache_create_tokens: 96_000,
    cost_usd: 3.42, cost_partial: false, billed_partial: false, estimated_cost_usd: null, ...overrides };
}

export function domainQuestion(overrides: Partial<ChunkQuestion> = {}): ChunkQuestion {
  return { question_id: 'qn_fixture', chunk_id: 'ch_fixture', runner_id: 'rn_fixture', epoch: 1,
    question: 'Which option?', options: ['First', 'Second'], answered: false, asked_at: '2026-07-15T00:00:00Z',
    answer: null, answered_at: null, answered_by: null, delivered: false, delivered_at: null, harness_id: null,
    node_id: null, project_id: null, project_name: null, project_slug: null, runner_name: null, session_id: null,
    superseded: false, ...overrides };
}

export function domainDecision(overrides: Partial<ChunkDecision> = {}): ChunkDecision {
  return { decision_id: 'dc_fixture', chunk_id: 'ch_fixture', node_id: 'nd_review', node_name: 'review', epoch: 1,
    choices: [{ name: 'approve', description: 'Accept', requires_checks: true, would_override_red_checks: true },
      { name: 'rework', description: 'Rework', requires_checks: false, would_override_red_checks: false }],
    check_results: [{ command: 'npm run test', passed: false }], submitted_at: '2026-07-15T00:00:00Z',
    imposed_by_runner_id: null, imposed_by_runner_name: null, note: null, overrode_red_checks: false, project_id: null,
    project_name: null, project_slug: null, resolved_at: null, resolved_by: null, resolved_choice: null, transitioned: false,
    ...overrides };
}

/** A complete detail aggregate for presentation fixtures. Derived fields follow the supplied facts. */
export function domainChunkDetail(overrides: Partial<Omit<ChunkDetail, 'status'>> & { status?: ChunkDetail['status'] | ChunkDetail['status']['kind']; questions?: ChunkQuestion[]; decision?: ChunkDecision | null } = {}): ChunkDetail {
  const { questions = [], decision = null, status, ...fields } = overrides;
  const base: ChunkDetail = {
    chunk_id: 'ch_fixture', graph_id: 'gr_build', graph_name: 'build', graph_created_at: null, graph_editable: false,
    status: { kind: 'running' }, status_if_paused: null, terminal: false, current_node_id: 'nd_build', current_node_name: 'build',
    current_node_terminal: false, latest_epoch: 1, artifacts: [], awaiting_external_merge: false, blocked: null, bounces: [],
    closed_prs: [], completable: false, cost: domainUsageTotal(), default_effort: null,
    default_harnesses: [], default_model: [], deletable: false, delete_refusal: null, escalation: null, flow_places: [],
    flow_record: [], history: [], intended_migration: null, landed: false, landed_repos: [], migrations: [],
    neighborhood: { prerequisites: [], dependents: [] }, open_prs: [], pausable: false, pause: null, pending: null,
    project_id: null, project_name: null, project_slug: null, promotable: false, repository_targets: [],
    requeues: [], resolutions: [], restarts: [], route: null, urgency: null, usage: [], work_refs: [],
    awaitingHuman: awaitingHuman({ chunk_id: 'ch_fixture', terminal: false, questions: [], decision: null }),
    transcriptContext: { history: [], current: { nodeId: 'nd_build', nodeName: 'build', epoch: 1, terminal: false } },
  };
  const detail = { ...base, ...fields, status: typeof status === 'string' ? (status === 'unknown' ? { kind: 'unknown' as const, raw: status } : { kind: status }) : status ?? base.status };
  return { ...detail,
    awaitingHuman: overrides.awaitingHuman ?? awaitingHuman({ chunk_id: detail.chunk_id, terminal: detail.terminal,
      questions, decision }),
    transcriptContext: overrides.transcriptContext ?? {
      history: detail.history.map((h) => ({ from_node_id: h.from_node_id, from_node_name: h.from_node_name, epoch: h.epoch })),
      current: { nodeId: detail.current_node_id, nodeName: detail.current_node_name, epoch: detail.latest_epoch, terminal: detail.current_node_terminal },
    },
  };
}

export function domainQueuedChunk(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return domainChunkDetail({ status: 'ready', current_node_id: null, current_node_name: null, latest_epoch: null, history: [], ...overrides });
}

export function domainMidFlightChunk(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return domainChunkDetail({ latest_epoch: 3, history: [
    domainTransition({ to_node_id: 'nd_plan', to_node_name: 'plan', epoch: 1, recorded_at: fixtureInstant(-900) }),
    domainTransition({ from_node_id: 'nd_plan', from_node_name: 'plan', choice_name: 'pass', epoch: 2, recorded_at: fixtureInstant(-600) }),
    domainTransition({ from_node_id: 'nd_build', from_node_name: 'build', choice_name: 'pass', epoch: 3, recorded_at: fixtureInstant(-60) }),
  ], usage: [domainUsage({ node_id: 'nd_plan', epoch: 1 }), domainUsage({ node_id: 'nd_build', epoch: 2, cost_usd: 1.24 })], ...overrides });
}

export function domainBouncedChunk(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return domainChunkDetail({ latest_epoch: 2, history: [
    domainTransition({ to_node_id: 'nd_build', to_node_name: 'build', epoch: 1, recorded_at: fixtureInstant(-600) }),
    domainTransition({ from_node_id: 'nd_build', from_node_name: 'build', to_node_id: 'nd_review', to_node_name: 'review', choice_name: 'pass', epoch: 1, recorded_at: fixtureInstant(-300) }),
    domainTransition({ from_node_id: 'nd_review', from_node_name: 'review', choice_name: 'fail', epoch: 2, recorded_at: fixtureInstant(-60) }),
  ], bounces: [domainBounce({ recorded_at: fixtureInstant(-50) })], usage: [domainUsage(), domainUsage({ node_id: 'nd_review', cost_usd: 0.31 })], ...overrides });
}

export function domainEscalatedChunk(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return domainChunkDetail({ status: 'needs_human', escalation: chunkEscalation(), ...overrides });
}

export function domainLandedChunk(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return domainChunkDetail({ status: 'done', terminal: true, landed: true, current_node_id: 'nd_done', current_node_name: 'done',
    current_node_terminal: true, landed_repos: [domainLandedRepo()], history: [domainTransition({ recorded_at: fixtureInstant(-600) }),
      domainTransition({ from_node_id: 'nd_build', from_node_name: 'build', to_node_id: 'nd_done', to_node_name: 'done',
        choice_name: 'pass', epoch: 1, recorded_at: fixtureInstant(-60) })], ...overrides });
}

export function domainStoppedChunk(overrides: Partial<ChunkDetail> = {}): ChunkDetail {
  return domainChunkDetail({ status: 'stopped', terminal: true, ...overrides });
}

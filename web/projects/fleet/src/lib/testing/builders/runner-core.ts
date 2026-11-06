import type { AskView, FactView, LeaseView } from '../../api/runner';
import { fixtureInstant } from './clock';

/** A live lease on `ch_1`, heartbeating. */
export function runnerLeaseView(overrides: Partial<LeaseView> = {}): LeaseView {
  return {
    lease_id: 'L-1',
    chunk_id: 'ch_1',
    graph_id: 'gr_1',
    node_id: 'nd_build',
    node_name: 'build',
    epoch: 1,
    session_id: null,
    pid: null,
    environment_id: null,
    workdir: null,
    project_id: null,
    harness_id: null,
    harness_version: null,
    resolved_effort: null,
    resolved_model: null,
    git_commits: [],
    created_at: fixtureInstant(-3600),
    last_heartbeat_at: fixtureInstant(-30),
    stale_after_seconds: 120,
    closed_at: null,
    closure_healthy: true,
    closure_reason: null,
    display_status: 'running',
    state: 'running',
    ...overrides,
  };
}

/** An open ask on lease `L-1`. */
export function runnerAskView(overrides: Partial<AskView> = {}): AskView {
  return {
    question_id: 'q-1',
    chunk_id: 'ch_1',
    project_id: null,
    lease_id: 'L-1',
    session_id: null,
    harness_id: null,
    question: '?',
    options: [],
    asked_at: fixtureInstant(-60),
    ...overrides,
  };
}

/** One ledger fact, unattributed and unacked. */
export function runnerFactView(overrides: Partial<FactView> = {}): FactView {
  return {
    seq: 1,
    kind: 'note',
    chunk_id: null,
    project_id: null,
    lease_id: null,
    created_at: fixtureInstant(-60),
    acked_at: null,
    ...overrides,
  };
}

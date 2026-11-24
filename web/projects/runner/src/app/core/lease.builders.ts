import { fixtureInstant } from 'fleet/testing';

import type { Lease } from './lease.domain';

/** A live lease on `ch_1`, heartbeating — the domain twin of `fleet/testing`'s `runnerLeaseView`. */
export function lease(overrides: Partial<Lease> = {}): Lease {
  const state = overrides.state ?? 'running';
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
    host_settings_revision: null,
    runner_settings_revision: null,
    git_commits: [],
    created_at: fixtureInstant(-3600),
    last_heartbeat_at: fixtureInstant(-30),
    stale_after_seconds: 120,
    closed_at: null,
    closure_healthy: true,
    closure_reason: null,
    display_status: 'running',
    state,
    liveness: state === 'exited' || state === 'closed' ? 'ended' : 'live',
    ...overrides,
  };
}

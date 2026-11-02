import type { runnerApi } from 'fleet';

import { sessionEnding } from './worker-session';

function lease(overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView {
  return {
    lease_id: 'L-1',
    chunk_id: 'ch_1',
    graph_id: 'gr_1',
    node_id: 'nd_1',
    node_name: 'build',
    epoch: 1,
    session_id: null,
    pid: null,
    environment_id: null,
    workdir: null,
    created_at: '2026-07-16T11:00:00+00:00',
    closed_at: null,
    closure_healthy: false,
    closure_reason: null,
    display_status: 'running',
    last_heartbeat_at: null,
    stale_after_seconds: 3600,
    resolved_model: null,
    resolved_effort: null,
    harness_version: null,
    git_commits: [],
    state: 'running',
    ...overrides,
  } as runnerApi.LeaseView;
}

describe('sessionEnding', () => {
  it('words a published closure reason, and shows an unknown one as itself', () => {
    expect(sessionEnding(lease({ state: 'closed', closure_reason: 'preempted' }))).toBe('restarted');
    expect(sessionEnding(lease({ state: 'closed', closure_reason: 'mystery' }))).toBe('mystery');
    expect(sessionEnding(lease({ state: 'closed', closure_reason: 'constructor' }))).toBe('constructor');
  });

  it('is null while live and exited once its worker exited', () => {
    expect(sessionEnding(lease())).toBeNull();
    expect(sessionEnding(lease({ state: 'exited' }))).toBe('exited');
  });
});

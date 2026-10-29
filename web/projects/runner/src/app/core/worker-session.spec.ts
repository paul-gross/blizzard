import { nodeStepKey, type runnerApi } from 'fleet';

import { sessionEnding, stepSessions } from './worker-session';

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

describe('stepSessions', () => {
  it('keeps every step across the runners that ran the chunk, the first lease per step winning', () => {
    const sessions = stepSessions([
      lease({ node_id: 'nd_2', epoch: 1, environment_id: 'on-runner-b', created_at: '2026-07-16T12:00:00+00:00' }),
      lease({ node_id: 'nd_1', epoch: 1, environment_id: 'on-runner-a', state: 'closed', closure_reason: 'preempted' }),
      lease({ node_id: 'nd_2', epoch: 1, environment_id: 'older-attempt', created_at: '2026-07-16T10:00:00+00:00' }),
    ]);

    expect(sessions.size).toBe(2);
    expect(sessions.get(nodeStepKey('nd_2', 1))?.environment).toBe('on-runner-b');
    expect(sessions.get(nodeStepKey('nd_1', 1))).toEqual(
      expect.objectContaining({ environment: 'on-runner-a', ending: 'restarted' }),
    );
  });
});

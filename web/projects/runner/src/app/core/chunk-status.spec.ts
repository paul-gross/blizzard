import type { runnerApi } from 'fleet';

import { deriveMachineChunkStatus } from './chunk-status';

function lease(overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView {
  return {
    lease_id: 'lease_1',
    chunk_id: 'ch_1',
    graph_id: 'gr_1',
    node_id: 'nd_build',
    node_name: 'build',
    epoch: 1,
    session_id: 'sess-1',
    pid: 1,
    environment_id: 'e1',
    workdir: '/ws/e1',
    created_at: '2026-07-16T11:00:00.000Z',
    last_heartbeat_at: '2026-07-16T11:59:00.000Z',
    state: 'running',
    display_status: 'running',
    closure_healthy: false,
    closed_at: null,
    closure_reason: null,
    stale_after_seconds: 3600,
    resolved_model: null,
    resolved_effort: null,
    harness_version: null,
    git_commits: [],
    harness_id: null,
    project_id: null,
    ...overrides,
  };
}

describe('deriveMachineChunkStatus', () => {
  it('maps the plain lease states', () => {
    expect(deriveMachineChunkStatus(lease({ state: 'running', display_status: 'running' }))).toEqual({
      label: 'RUNNING',
      tone: 'running',
    });
    expect(deriveMachineChunkStatus(lease({ state: 'stale', display_status: 'stale' }))).toEqual({
      label: 'STALE',
      tone: 'stale',
    });
    expect(deriveMachineChunkStatus(lease({ state: 'backing-off', display_status: 'backing-off' }))).toEqual({
      label: 'BACKING OFF',
      tone: 'waiting',
    });
    expect(deriveMachineChunkStatus(lease({ state: 'spawning', display_status: 'spawning' }))).toEqual({
      label: 'SPAWNING',
      tone: 'spawning',
    });
    expect(deriveMachineChunkStatus(lease({ state: 'exited', display_status: 'exited' }))).toEqual({
      label: 'WORKER EXITED',
      tone: 'idle',
    });
  });

  it('reads a healthy closure as the done tone, other closures dim', () => {
    const closed = { state: 'closed', display_status: 'closed' } as const;
    expect(
      deriveMachineChunkStatus(lease({ ...closed, closure_reason: 'transitioned', closure_healthy: true })),
    ).toEqual({ label: 'COMPLETED', tone: 'done' });
    expect(deriveMachineChunkStatus(lease({ ...closed, closure_reason: 'failed' }))).toEqual({
      label: 'ENDED · FAILED',
      tone: 'idle',
    });
  });

  it('follows the wire display_status for a chunk-level human fact, whatever the lease state', () => {
    expect(deriveMachineChunkStatus(lease({ state: 'parked', display_status: 'waiting-ask' }))).toEqual({
      label: 'WAITING · ASK',
      tone: 'waiting',
    });
    expect(deriveMachineChunkStatus(lease({ state: 'closed', display_status: 'needs-human' }))).toEqual({
      label: 'NEEDS HUMAN',
      tone: 'needs',
    });
    expect(deriveMachineChunkStatus(lease({ display_status: 'human-in-session' }))).toEqual({
      label: 'HUMAN IN SESSION',
      tone: 'takeover',
    });
  });
});

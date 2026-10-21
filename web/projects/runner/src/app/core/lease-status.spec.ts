import type { runnerApi } from 'fleet';

import { deriveLeaseStatus } from './lease-status';

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
    harness_id: null,
    project_id: null,
    ...overrides,
  };
}

describe('deriveLeaseStatus', () => {
  it('maps every live lease state to its label and tone', () => {
    expect(deriveLeaseStatus(lease({ state: 'running' }))).toEqual({ label: 'RUNNING', tone: 'running' });
    expect(deriveLeaseStatus(lease({ state: 'stale' }))).toEqual({ label: 'STALE', tone: 'stale' });
    expect(deriveLeaseStatus(lease({ state: 'parked' }))).toEqual({ label: 'PARKED', tone: 'waiting' });
    expect(deriveLeaseStatus(lease({ state: 'backing-off' }))).toEqual({ label: 'BACKING OFF', tone: 'waiting' });
    expect(deriveLeaseStatus(lease({ state: 'spawning' }))).toEqual({ label: 'SPAWNING', tone: 'spawning' });
    expect(deriveLeaseStatus(lease({ state: 'exited' }))).toEqual({ label: 'WORKER EXITED', tone: 'idle' });
  });

  it('reads a healthy closure as completed and any other closure dim, naming how its session ended', () => {
    expect(deriveLeaseStatus(lease({ state: 'closed', closure_reason: 'transitioned', closure_healthy: true }))).toEqual({
      label: 'COMPLETED',
      tone: 'done',
    });
    expect(deriveLeaseStatus(lease({ state: 'closed', closure_reason: 'reaped' }))).toEqual({
      label: 'ENDED · TIMED OUT',
      tone: 'idle',
    });
    expect(deriveLeaseStatus(lease({ state: 'closed', closure_reason: null }))).toEqual({
      label: 'ENDED · UNKNOWN',
      tone: 'idle',
    });
  });
});

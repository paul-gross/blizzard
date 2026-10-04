import { compactRef, type runnerApi } from 'fleet';

import { heartbeatLabel, leaseRefLabel } from './chunk-detail.model';

const NOW = Date.parse('2026-07-16T12:00:00.000Z');

function lease(overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView {
  return {
    lease_id: 'ls_01KXKVVF1J3D6H6VYZ3XYN3AB1',
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
    last_heartbeat_at: '2026-07-16T11:59:26.000Z',
    state: 'running',
    closed_at: null,
    closure_reason: null,
    stale_after_seconds: 3600,
    ...overrides,
  };
}

describe('leaseRefLabel', () => {
  it('renders the lease compact ref', () => {
    expect(leaseRefLabel(lease())).toBe(compactRef('ls_01KXKVVF1J3D6H6VYZ3XYN3AB1'));
  });

  it('renders blank with no lease', () => {
    expect(leaseRefLabel(null)).toBe('');
  });
});

describe('heartbeatLabel', () => {
  it('renders the age of the last beat', () => {
    expect(heartbeatLabel(lease(), NOW)).toBe('-34s');
  });

  it('renders an em dash with no lease or a closed lease', () => {
    expect(heartbeatLabel(null, NOW)).toBe('—');
    expect(heartbeatLabel(lease({ state: 'closed' }), NOW)).toBe('—');
  });

  it('renders an em dash before the first beat or past the skew bound', () => {
    expect(heartbeatLabel(lease({ last_heartbeat_at: null }), NOW)).toBe('—');
    expect(heartbeatLabel(lease({ last_heartbeat_at: '2026-07-16T13:00:00.000Z' }), NOW)).toBe('—');
  });
});

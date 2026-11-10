import { compactRef, runnerClient, type runnerApi } from 'fleet';

import type { PanelLease } from '../../core/panel-reads.model';
import {
  heartbeatLabel,
  leaseRefLabel,
  liveWorker,
  type RequeueFeedback,
  requeueErrorOf,
  requeueFeedbackFor,
  requeueKey,
  runnerTarget,
  withRequeueFeedback,
} from './chunk-detail.model';

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
    liveness: 'live',
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

function panelLease(overrides: Partial<runnerApi.LeaseView> = {}): PanelLease {
  return { ...lease(overrides), owner: { runnerId: 'rn_1', name: 'r1', label: 'r1' }, projectSlug: null };
}

describe('runnerTarget', () => {
  it("targets the named runner's own API", () => {
    expect(runnerTarget('rn_1')).toEqual({ plane: 'runner', client: runnerClient, runnerId: 'rn_1' });
  });

  it('targets nothing while no chunk is selected', () => {
    expect(runnerTarget(null)).toBeNull();
  });
});

describe('liveWorker', () => {
  it('is the newest lease while its session has not ended', () => {
    const running = panelLease();
    const parked = panelLease({ state: 'parked' });
    expect(liveWorker(running)).toBe(running);
    expect(liveWorker(parked)).toBe(parked);
  });

  it('is null once the worker exited or the session closed', () => {
    expect(liveWorker(panelLease({ state: 'exited', liveness: 'ended' }))).toBeNull();
    expect(liveWorker(panelLease({ state: 'closed', liveness: 'ended' }))).toBeNull();
  });

  it('is null with nothing selected', () => {
    expect(liveWorker(null)).toBeNull();
  });
});

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
    expect(heartbeatLabel(lease({ state: 'closed', liveness: 'ended' }), NOW)).toBe('—');
    expect(heartbeatLabel(lease({ state: 'exited', liveness: 'ended' }), NOW)).toBe('—');
    expect(heartbeatLabel(lease({ liveness: 'mystery' as runnerApi.LeaseLiveness }), NOW)).toBe('-34s');
  });

  it('renders an em dash before the first beat or past the skew bound', () => {
    expect(heartbeatLabel(lease({ last_heartbeat_at: null }), NOW)).toBe('—');
    expect(heartbeatLabel(lease({ last_heartbeat_at: '2026-07-16T13:00:00.000Z' }), NOW)).toBe('—');
  });
});

describe('requeueFeedbackFor', () => {
  const REQUEUED: RequeueFeedback = { kind: 'requeued' };
  const FAILED: RequeueFeedback = { kind: 'error', message: 'refused' };
  const feedback = new Map<string, RequeueFeedback>([
    [requeueKey('rn_1', 'ch_a'), REQUEUED],
    [requeueKey('rn_2', 'ch_a'), FAILED],
  ]);

  it('reads the entry held under the runner and chunk together', () => {
    expect(requeueFeedbackFor(feedback, 'rn_1', 'ch_a')).toBe(REQUEUED);
    expect(requeueFeedbackFor(feedback, 'rn_2', 'ch_a')).toBe(FAILED);
  });

  it('is null for a chunk with no entry, or another runner\'s chunk of the same id', () => {
    expect(requeueFeedbackFor(feedback, 'rn_1', 'ch_b')).toBeNull();
    expect(requeueFeedbackFor(feedback, 'rn_3', 'ch_a')).toBeNull();
  });

  it('is null while no runner or chunk is selected', () => {
    expect(requeueFeedbackFor(feedback, null, 'ch_a')).toBeNull();
    expect(requeueFeedbackFor(feedback, 'rn_1', null)).toBeNull();
  });

  it('keeps runner and chunk ids from running together into one key', () => {
    expect(requeueKey('rn_1', 'ch_a')).not.toBe(requeueKey('rn_1c', 'h_a'));
  });
});

describe('withRequeueFeedback', () => {
  it('sets one key without touching another, and clears it on null', () => {
    const other = new Map([[requeueKey('rn_2', 'ch_a'), { kind: 'requeued' } as RequeueFeedback]]);
    const set = withRequeueFeedback(other, 'rn_1', 'ch_a', { kind: 'error', message: 'x' });
    expect(requeueFeedbackFor(set, 'rn_1', 'ch_a')).toEqual({ kind: 'error', message: 'x' });
    expect(requeueFeedbackFor(set, 'rn_2', 'ch_a')).toEqual({ kind: 'requeued' });
    expect(other.size).toBe(1);
    const cleared = withRequeueFeedback(set, 'rn_1', 'ch_a', null);
    expect(requeueFeedbackFor(cleared, 'rn_1', 'ch_a')).toBeNull();
    expect(requeueFeedbackFor(cleared, 'rn_2', 'ch_a')).toEqual({ kind: 'requeued' });
  });
});

describe('requeueErrorOf', () => {
  it('reads a failure\'s message only', () => {
    expect(requeueErrorOf({ kind: 'error', message: 'refused' })).toBe('refused');
    expect(requeueErrorOf({ kind: 'requeued' })).toBeNull();
    expect(requeueErrorOf(null)).toBeNull();
  });
});

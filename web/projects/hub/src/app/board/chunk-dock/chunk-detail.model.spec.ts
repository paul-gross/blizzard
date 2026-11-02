import { type ChunkDetail, type ChunkStatus, type WorkItemsQuery } from 'fleet';
import { describe, expect, it } from 'vitest';

import { openDetail, openWorkItems, pendingQuestionIds, pendingStatusOverride } from './chunk-detail.model';

const detail = (status: ChunkStatus, chunkId = 'ch_a', statusIfPaused?: ChunkStatus): ChunkDetail =>
  ({ chunk_id: chunkId, status, status_if_paused: statusIfPaused }) as ChunkDetail;

const STATUSES: readonly ChunkStatus[] = [
  'not_ready',
  'ready',
  'running',
  'delivering',
  'waiting_on_human',
  'needs_human',
  'paused',
  'stopped',
  'done',
];

describe('pendingStatusOverride', () => {
  it('overrides nothing without an open detail or a pending mutation', () => {
    expect(pendingStatusOverride(undefined, [{ chunkId: 'ch_a' }], [])).toBeNull();
    expect(pendingStatusOverride(detail('running'), [], [])).toBeNull();
  });

  it('predicts done while Complete is pending, ahead of a pending Pause', () => {
    expect(pendingStatusOverride(detail('needs_human'), [{ chunkId: 'ch_a' }], [{ chunkId: 'ch_a', paused: true }])).toBe('done');
  });

  it('predicts paused exactly where the hub settles the pause to paused', () => {
    const predicted = STATUSES.map((settled) => [
      settled,
      pendingStatusOverride(detail('running', 'ch_a', settled), [], [{ chunkId: 'ch_a', paused: true }]),
    ]);
    expect(Object.fromEntries(predicted)).toEqual({
      not_ready: null,
      ready: null,
      running: null,
      delivering: null,
      waiting_on_human: null,
      needs_human: null,
      paused: 'paused',
      stopped: null,
      done: null,
    });
  });

  it('predicts nothing for the human-gated ones, whose gate outranks the pause', () => {
    for (const gated of ['waiting_on_human', 'needs_human'] as const) {
      expect(pendingStatusOverride(detail(gated, 'ch_a', gated), [], [{ chunkId: 'ch_a', paused: true }])).toBeNull();
    }
  });

  it('predicts nothing where the hub refuses the pause, or names no settled status', () => {
    for (const refused of ['delivering', 'stopped', 'done'] as const) {
      expect(pendingStatusOverride(detail(refused, 'ch_a', refused), [], [{ chunkId: 'ch_a', paused: true }])).toBeNull();
    }
    expect(pendingStatusOverride(detail('running'), [], [{ chunkId: 'ch_a', paused: true }])).toBeNull();
  });

  it('predicts nothing for a pending Resume', () => {
    expect(pendingStatusOverride(detail('paused', 'ch_a', 'paused'), [], [{ chunkId: 'ch_a', paused: false }])).toBeNull();
  });

  it('ignores mutations pending for another chunk', () => {
    expect(pendingStatusOverride(detail('running', 'ch_a', 'paused'), [{ chunkId: 'ch_b' }], [{ chunkId: 'ch_b', paused: true }])).toBeNull();
  });
});

describe('pendingQuestionIds', () => {
  it('lists each pending answer question id in order', () => {
    expect(pendingQuestionIds([{ questionId: 'q_2' }, { questionId: 'q_1' }])).toEqual(['q_2', 'q_1']);
  });
});

describe('openDetail', () => {
  it('withholds the aggregate while the dock is closed', () => {
    expect(openDetail(null, detail('running'))).toBeUndefined();
  });

  it('passes the aggregate through for an open chunk', () => {
    const open = detail('running');
    expect(openDetail('ch_a', open)).toBe(open);
    expect(openDetail('ch_a', undefined)).toBeUndefined();
  });
});

describe('openWorkItems', () => {
  const query = (state: 'pending' | 'error' | 'success'): WorkItemsQuery =>
    ({
      isPending: () => state === 'pending',
      isError: () => state === 'error',
      data: () => (state === 'success' ? { items: [{ id: 'wi_1' }] } : undefined),
    }) as unknown as WorkItemsQuery;

  it('rests in loading while the dock is closed, whatever the query holds', () => {
    expect(openWorkItems(null, query('success'))).toEqual({ status: 'loading', items: [] });
  });

  it('folds the open chunk query triad', () => {
    expect(openWorkItems('ch_a', query('pending'))).toEqual({ status: 'loading', items: [] });
    expect(openWorkItems('ch_a', query('error'))).toEqual({ status: 'error', items: [] });
    expect(openWorkItems('ch_a', query('success'))).toEqual({ status: 'success', items: [{ id: 'wi_1' }] });
  });
});

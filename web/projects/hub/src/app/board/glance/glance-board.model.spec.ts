import { type ChunkCountsView, type ChunkSummary, type DecisionView, type QuestionView, type QueuePeekEntry, type RunnerView } from 'fleet';
import { describe, expect, it } from 'vitest';

import {
  doneTodayRows,
  doneTodayTotal,
  glanceVitals,
  inMotionRows,
  liveRunners,
  needsYouRows,
  terminalTotal,
  upNextRows,
} from './glance-board.model';

const NOW = Date.parse('2026-06-01T12:00:00Z');
const HOUR = 60 * 60 * 1000;
const ago = (ms: number): string => new Date(NOW - ms).toISOString();

const chunk = (id: string, status: ChunkSummary['status'], fields: Partial<ChunkSummary> = {}): ChunkSummary => ({
  chunk_id: id,
  graph_id: 'gr_1',
  status,
  current_node_id: 'nd_build',
  ...fields,
});

const question = (chunkId: string, text: string, runnerId: string | null = null): QuestionView =>
  ({ chunk_id: chunkId, question: text, runner_id: runnerId }) as QuestionView;

const decision = (chunkId: string, nodeName: string): DecisionView => ({ chunk_id: chunkId, node_name: nodeName }) as DecisionView;

const runner = (id: string, fields: Partial<RunnerView>): RunnerView => ({ runner_id: id, ...fields }) as RunnerView;

const queued = (chunkId: string): QueuePeekEntry => ({ chunk_id: chunkId }) as QueuePeekEntry;

describe('liveRunners', () => {
  it('drops retired runners and keeps the rest in order', () => {
    const rows = liveRunners([runner('r1', { retired: false }), runner('r2', { retired: true }), runner('r3', {})]);
    expect(rows.map((r) => r.runner_id)).toEqual(['r1', 'r3']);
  });
});

describe('needsYouRows', () => {
  it('orders asks, then gates, then human-attention chunks, deduped by chunk id', () => {
    const rows = needsYouRows(
      [question('ch_ask', 'Which branch?', 'r1')],
      [decision('ch_ask', 'review'), decision('ch_gate', 'approve')],
      [
        chunk('ch_ask', 'waiting_on_human'),
        chunk('ch_gate', 'waiting_on_human', { runner_id: 'r2' }),
        chunk('ch_needs', 'needs_human', { current_node_name: 'Build' }),
        chunk('ch_waiting', 'waiting_on_human', { current_node_id: null }),
        chunk('ch_running', 'running'),
      ],
    );
    expect(rows).toEqual([
      { chunkId: 'ch_ask', shortId: expect.any(String), runnerId: 'r1', tone: 'waiting', pillLabel: 'ask', sub: 'Which branch?' },
      { chunkId: 'ch_gate', shortId: expect.any(String), runnerId: 'r2', tone: 'waiting', pillLabel: 'gate', sub: 'approve' },
      { chunkId: 'ch_needs', shortId: expect.any(String), runnerId: null, tone: 'needs', pillLabel: 'needs human', sub: 'Build' },
      { chunkId: 'ch_waiting', shortId: expect.any(String), runnerId: null, tone: 'waiting', pillLabel: 'waiting', sub: '—' },
    ]);
  });

  it('leaves a gate on an unlisted chunk unrouted', () => {
    expect(needsYouRows([], [decision('ch_gone', 'approve')], [])[0]?.runnerId).toBeNull();
  });
});

describe('inMotionRows', () => {
  it('keeps the running lane, labels delivering, and zero-fills a missing cost', () => {
    const rows = inMotionRows([
      chunk('ch_run', 'running', {
        runner_id: 'r1',
        current_node_name: 'Build',
        cost: { cost_usd: 1.5, cost_partial: true, estimated_cost_usd: 2 } as ChunkSummary['cost'],
      }),
      chunk('ch_deliver', 'delivering'),
      chunk('ch_ready', 'ready'),
    ]);
    expect(rows).toEqual([
      expect.objectContaining({ chunkId: 'ch_run', runnerId: 'r1', node: 'Build', pillLabel: 'run', costUsd: 1.5, costPartial: true, estimatedCostUsd: 2 }),
      expect.objectContaining({ chunkId: 'ch_deliver', runnerId: null, node: 'nd_build', pillLabel: 'deliver', costUsd: 0, costPartial: false, estimatedCostUsd: null }),
    ]);
  });
});

describe('upNextRows', () => {
  it('follows the queue order and drops entries whose chunk is missing or no longer ready', () => {
    const rows = upNextRows(
      [queued('ch_b'), queued('ch_gone'), queued('ch_running'), queued('ch_a')],
      [chunk('ch_a', 'ready'), chunk('ch_b', 'ready', { current_node_name: 'Plan' }), chunk('ch_running', 'running')],
    );
    expect(rows.map((r) => [r.chunkId, r.node])).toEqual([
      ['ch_b', 'Plan'],
      ['ch_a', 'nd_build'],
    ]);
  });
});

describe('doneTodayRows', () => {
  it('keeps terminal chunks completed in the last 24 hours, newest first', () => {
    const rows = doneTodayRows(
      [
        chunk('ch_older', 'stopped', { terminal: true, completed_at: ago(3 * HOUR) }),
        chunk('ch_newer', 'done', { terminal: true, completed_at: ago(HOUR), work_refs: [{ label: 'a#1' }, { label: null }, { label: 'b#2' }] as ChunkSummary['work_refs'] }),
        chunk('ch_stale', 'done', { terminal: true, completed_at: ago(25 * HOUR) }),
        chunk('ch_undated', 'done', { terminal: true, completed_at: null }),
        chunk('ch_live', 'running', { completed_at: ago(HOUR) }),
      ],
      NOW,
    );
    expect(rows).toEqual([
      { chunkId: 'ch_newer', shortId: expect.any(String), pointerLabel: 'a#1 b#2' },
      { chunkId: 'ch_older', shortId: expect.any(String), pointerLabel: '' },
    ]);
  });

  it('reads terminality from the wire flag, not the status', () => {
    expect(doneTodayRows([chunk('ch_x', 'done', { terminal: false, completed_at: ago(HOUR) })], NOW)).toEqual([]);
  });
});

describe('terminalTotal', () => {
  it('reads the hub terminal count, 0 before the counts resolve', () => {
    expect(terminalTotal({ terminal: 207, done: 1, stopped: 1 } as ChunkCountsView)).toBe(207);
    expect(terminalTotal(undefined)).toBe(0);
  });
});

describe('doneTodayTotal', () => {
  it('withholds the denominator while the counts read is pending or failed', () => {
    expect(doneTodayTotal(true, false, 4)).toBeNull();
    expect(doneTodayTotal(false, true, 4)).toBeNull();
    expect(doneTodayTotal(false, false, 0)).toBe(0);
  });
});

describe('glanceVitals', () => {
  const runners = [runner('r1', { online: true }), runner('r2', { online: false })];

  it('carries the counts, the online fraction, and a live stream', () => {
    expect(glanceVitals(runners, 'open', true, 3, 2)).toEqual({
      needsYou: 3,
      running: 2,
      runnersUpLabel: '1/2',
      live: true,
      liveLabel: 'live',
    });
  });

  it('labels each non-open connection state', () => {
    expect(glanceVitals([], 'reconnecting', true, 0, 0)).toMatchObject({ live: false, liveLabel: 'reconnecting', runnersUpLabel: '0/0' });
    expect(glanceVitals([], 'idle', true, 0, 0).liveLabel).toBe('offline');
    expect(glanceVitals([], 'closed', false, 0, 0).liveLabel).toBe('connecting');
  });
});

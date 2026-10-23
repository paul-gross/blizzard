import { type ChunkCountsView, type ChunkSummary, type DecisionView, type QuestionView, type QueuePeekEntry, type RunnerRegistryView, type WorkItemsView } from 'fleet';
import { chunkUsageTotalView } from 'fleet/testing';
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

const chunkSummary = (overrides: Pick<ChunkSummary, 'chunk_id' | 'graph_id' | 'status'> & Partial<ChunkSummary>): ChunkSummary => ({
  awaiting_external_merge: false,
  blocked: null,
  closed_prs: [],
  completed_at: null,
  cost: chunkUsageTotalView({ input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0, cost_usd: 0 }),
  current_node_id: null,
  current_node_name: null,
  default_effort: null,
  default_harnesses: [],
  default_model: [],
  environment_count: 0,
  landed: false,
  landed_repos: [],
  open_prs: [],
  project_id: null,
  project_name: null,
  project_slug: null,
  promotable: false,
  runner_id: null,
  runner_name: null,
  terminal: false,
  work_refs: [],
  ...overrides,
});


/** No chunk's work items read yet. */
const NO_ITEMS: ReadonlyMap<string, WorkItemsView> = new Map();

const NOW = Date.parse('2026-06-01T12:00:00Z');
const HOUR = 60 * 60 * 1000;
const ago = (ms: number): string => new Date(NOW - ms).toISOString();

const chunk = (id: string, status: ChunkSummary['status'], fields: Partial<ChunkSummary> = {}): ChunkSummary => (chunkSummary({
  chunk_id: id,
  graph_id: 'gr_1',
  status,
  current_node_id: 'nd_build',
  ...fields,
}));

const question = (chunkId: string, text: string, runnerId: string | null = null): QuestionView =>
  ({ chunk_id: chunkId, question: text, runner_id: runnerId }) as QuestionView;

const decision = (chunkId: string, nodeName: string): DecisionView => ({ chunk_id: chunkId, node_name: nodeName }) as DecisionView;

const runner = (id: string, fields: Partial<RunnerRegistryView>): RunnerRegistryView => ({ runner_id: id, ...fields }) as RunnerRegistryView;

const queued = (chunkId: string): QueuePeekEntry => ({ chunk_id: chunkId }) as QueuePeekEntry;

describe('liveRunners', () => {
  it('drops retired runners and keeps the rest in order', () => {
    const rows = liveRunners([runner('r1', { retired: false }), runner('r2', { retired: true }), runner('r3', {})]);
    expect(rows.map((r) => r.runner_id)).toEqual(['r1', 'r3']);
  });

  it('drops a never-connected runner: added but never started, it is not down', () => {
    const rows = liveRunners([
      runner('r1', { connection: 'online' }),
      runner('r2', { connection: 'never_connected' }),
      runner('r3', { connection: 'offline' }),
    ]);
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
      NO_ITEMS,
    );
    expect(rows).toEqual([
      expect.objectContaining({ chunkId: 'ch_ask', status: null, sub: 'Which branch?' }),
      expect.objectContaining({ chunkId: 'ch_gate', status: 'gate', sub: 'approve' }),
      expect.objectContaining({ chunkId: 'ch_needs', status: null, sub: 'Build' }),
      expect.objectContaining({ chunkId: 'ch_waiting', status: null, sub: '—' }),
    ]);
  });

  it('names a parked chunk\'s status, which the panel heading does not', () => {
    const rows = needsYouRows([], [], [chunk('ch_paused', 'paused')], NO_ITEMS);
    expect(rows.map((r) => [r.chunkId, r.status])).toEqual([['ch_paused', 'paused']]);
  });


});

describe('inMotionRows', () => {
  it('keeps the running lane, labels delivering, and zero-fills a missing cost', () => {
    const rows = inMotionRows(
      [
        chunk('ch_run', 'running', {
          runner_id: 'r1',
          current_node_name: 'Build',
          cost: { cost_usd: 1.5, cost_partial: true, estimated_cost_usd: 2 } as ChunkSummary['cost'],
        }),
        chunk('ch_deliver', 'delivering'),
        chunk('ch_ready', 'ready'),
      ],
      NO_ITEMS,
    );
    expect(rows).toEqual([
      expect.objectContaining({ chunkId: 'ch_run', node: 'Build', status: null, costUsd: 1.5, costPartial: true, estimatedCostUsd: 2 }),
      expect.objectContaining({ chunkId: 'ch_deliver', node: 'nd_build', status: 'delivering', costUsd: 0, costPartial: false, estimatedCostUsd: null }),
    ]);
  });

});

describe('upNextRows', () => {
  it('follows the queue order and drops entries whose chunk is missing or no longer ready', () => {
    const rows = upNextRows(
      [queued('ch_b'), queued('ch_gone'), queued('ch_running'), queued('ch_a')],
      [chunk('ch_a', 'ready'), chunk('ch_b', 'ready', { current_node_name: 'Plan' }), chunk('ch_running', 'running')],
      NO_ITEMS,
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
        chunk('ch_newer', 'done', { terminal: true, completed_at: ago(HOUR) }),
        chunk('ch_stale', 'done', { terminal: true, completed_at: ago(25 * HOUR) }),
        chunk('ch_undated', 'done', { terminal: true, completed_at: null }),
        chunk('ch_live', 'running', { completed_at: ago(HOUR) }),
      ],
      NOW,
      NO_ITEMS,
    );
    expect(rows).toEqual([
      expect.objectContaining({ chunkId: 'ch_newer', status: null }),
      expect.objectContaining({ chunkId: 'ch_older', status: 'stopped' }),
    ]);
  });

  it('reads terminality from the wire flag, not the status', () => {
    expect(doneTodayRows([chunk('ch_x', 'done', { terminal: false, completed_at: ago(HOUR) })], NOW, NO_ITEMS)).toEqual([]);
  });
});

describe('row work items', () => {
  const refs = [
    { source: 'wsrc_api', ref: '1', label: 'toy-api#1' },
    { source: 'wsrc_web', ref: '3', label: 'toy-web#3' },
  ] as ChunkSummary['work_refs'];
  const read = new Map([
    ['ch_1', { items: [{ source: 'wsrc_api', ref: '1', title: 'Rate-limit login' }, { source: 'wsrc_web', ref: '3', title: 'Drop moment.js' }] } as WorkItemsView],
  ]);

  it('leads every panel\'s row with each work item\'s label and title, as a board card does', () => {
    const expected = [
      { label: 'toy-api#1', title: 'Rate-limit login', url: null },
      { label: 'toy-web#3', title: 'Drop moment.js', url: null },
    ];
    const lines = [
      needsYouRows([], [], [chunk('ch_1', 'needs_human', { work_refs: refs })], read)[0],
      inMotionRows([chunk('ch_1', 'running', { work_refs: refs })], read)[0],
      upNextRows([queued('ch_1')], [chunk('ch_1', 'ready', { work_refs: refs })], read)[0],
      doneTodayRows([chunk('ch_1', 'done', { work_refs: refs, terminal: true, completed_at: ago(HOUR) })], NOW, read)[0],
    ];
    for (const row of lines) expect(row).toMatchObject({ lines: expected, itemsRead: true });
  });

  it('holds each labeled line, title unread, until the chunk\'s items are read', () => {
    const [row] = inMotionRows([chunk('ch_1', 'running', { work_refs: refs })], NO_ITEMS);
    expect(row).toMatchObject({ itemsRead: false, lines: [{ label: 'toy-api#1', title: null }, { label: 'toy-web#3', title: null }] });
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

  it('counts neither up nor total a runner that has never connected', () => {
    const fleet = [
      runner('r1', { online: true, connection: 'online' }),
      runner('r2', { online: false, connection: 'offline' }),
      runner('r3', { online: false, connection: 'never_connected' }),
    ];
    expect(glanceVitals(liveRunners(fleet), 'open', false, 0, 0).runnersUpLabel).toBe('1/2');
  });

  it('labels each non-open connection state', () => {
    expect(glanceVitals([], 'reconnecting', true, 0, 0)).toMatchObject({ live: false, liveLabel: 'reconnecting', runnersUpLabel: '0/0' });
    expect(glanceVitals([], 'idle', true, 0, 0).liveLabel).toBe('offline');
    expect(glanceVitals([], 'closed', false, 0, 0).liveLabel).toBe('connecting');
  });
});

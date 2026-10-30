import { type ChunkSummary, type DecisionView, type QuestionView, type RunnerRegistryView, type WorkItemsView } from 'fleet';
import { chunkSummary } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { glanceListGroups, glanceVitals, liveRunners } from './glance-board.model';

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

const question = (chunkId: string, text: string): QuestionView => ({ chunk_id: chunkId, question: text }) as QuestionView;

const decision = (chunkId: string): DecisionView => ({ chunk_id: chunkId }) as DecisionView;

const runner = (id: string, fields: Partial<RunnerRegistryView>): RunnerRegistryView => ({ runner_id: id, ...fields }) as RunnerRegistryView;

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

/** {@link glanceListGroups} with no asks, gates, ordering, hosts, or colors unless a case supplies them. */
const groups = (
  chunks: readonly ChunkSummary[],
  fields: {
    questions?: readonly QuestionView[];
    decisions?: readonly DecisionView[];
    readyOrder?: readonly string[];
    backlogOrder?: readonly string[];
    hosts?: ReadonlyMap<string, string>;
    colors?: ReadonlyMap<string, string>;
    workItems?: ReadonlyMap<string, WorkItemsView>;
  } = {},
) =>
  glanceListGroups(
    chunks,
    fields.questions ?? [],
    fields.decisions ?? [],
    (fields.readyOrder ?? []).map((chunkId) => ({ chunk_id: chunkId })),
    (fields.backlogOrder ?? []).map((chunkId) => ({ chunk_id: chunkId })),
    fields.hosts ?? new Map(),
    fields.colors ?? new Map(),
    NOW,
    fields.workItems ?? NO_ITEMS,
  );

const ids = (rows: readonly { chunkId: string }[]): string[] => rows.map((row) => row.chunkId);

describe('glanceListGroups', () => {
  it('leads with needs, then waiting, then running, then an unknown status', () => {
    const list = groups([
      chunk('ch_run', 'running'),
      chunk('ch_odd', 'mystery' as ChunkSummary['status']),
      chunk('ch_wait', 'waiting_on_human'),
      chunk('ch_needs', 'needs_human'),
      chunk('ch_deliver', 'delivering'),
    ]);
    expect(ids(list.live)).toEqual(['ch_needs', 'ch_wait', 'ch_run', 'ch_deliver', 'ch_odd']);
  });

  it('orders READY by the queue and BACKLOG by the backlog, an unranked chunk last in its own order', () => {
    const list = groups(
      [
        chunk('ch_r1', 'ready'),
        chunk('ch_r2', 'ready'),
        chunk('ch_r3', 'ready'),
        chunk('ch_b1', 'not_ready'),
        chunk('ch_b2', 'not_ready'),
        chunk('ch_b3', 'not_ready'),
      ],
      { readyOrder: ['ch_r3', 'ch_r1'], backlogOrder: ['ch_b2'] },
    );
    expect(ids(list.ready)).toEqual(['ch_r3', 'ch_r1', 'ch_r2']);
    expect(ids(list.backlog)).toEqual(['ch_b2', 'ch_b1', 'ch_b3']);
  });

  it('keeps only the chunks finished in the last 24 hours', () => {
    const list = groups([
      chunk('ch_recent', 'done', { terminal: true, completed_at: ago(HOUR) }),
      chunk('ch_stopped', 'stopped', { terminal: true, completed_at: ago(3 * HOUR) }),
      chunk('ch_stale', 'done', { terminal: true, completed_at: ago(25 * HOUR) }),
      chunk('ch_undated', 'done', { terminal: true, completed_at: null }),
    ]);
    expect(ids(list.done)).toEqual(['ch_recent', 'ch_stopped']);
  });

  it('marks an open gate and carries an open ask\'s question', () => {
    const [row] = groups([chunk('ch_1', 'waiting_on_human')], {
      questions: [question('ch_1', 'Which branch?')],
      decisions: [decision('ch_1')],
    }).live;
    expect(row).toMatchObject({ gated: true, ask: 'Which branch?' });
    expect(groups([chunk('ch_2', 'running')]).live[0]).toMatchObject({ gated: false, ask: null });
  });

  it('words only a paused or stopped status, which its lane icon cannot tell apart', () => {
    const list = groups([
      chunk('ch_paused', 'paused'),
      chunk('ch_run', 'running'),
      chunk('ch_deliver', 'delivering'),
      chunk('ch_stopped', 'stopped', { terminal: true, completed_at: ago(HOUR) }),
    ]);
    const words = [...list.live, ...list.done].map((row) => [row.chunkId, row.statusWord]);
    expect(words).toEqual(expect.arrayContaining([
      ['ch_paused', 'paused'],
      ['ch_run', null],
      ['ch_deliver', null],
      ['ch_stopped', 'stopped'],
    ]));
  });

  it('names the node, the holding runner by host and name, the cost, and the project color', () => {
    const [row] = groups(
      [
        chunk('ch_1', 'running', {
          current_node_name: 'Build',
          runner_id: 'rn_1',
          runner_name: 'r-claude',
          project_id: 'pj_1',
          cost: { cost_usd: 1.5, cost_partial: true, estimated_cost_usd: 2 } as ChunkSummary['cost'],
        }),
      ],
      { hosts: new Map([['rn_1', 'atlas']]), colors: new Map([['pj_1', 'teal']]) },
    ).live;
    expect(row).toMatchObject({
      node: 'Build',
      runner: { host: 'atlas', name: 'r-claude' },
      costUsd: 1.5,
      costPartial: true,
      estimatedCostUsd: 2,
      projectColor: 'teal',
    });
  });

  it('falls back to the node id, no runner, a zero cost, and no tint', () => {
    const [row] = groups([chunk('ch_1', 'running', { cost: undefined as unknown as ChunkSummary['cost'] })]).live;
    expect(row).toMatchObject({ node: 'nd_build', runner: null, costUsd: 0, costPartial: false, estimatedCostUsd: null, projectColor: null });
  });

  it('leads each card with its work items\' labels and titles, as a board card does', () => {
    const refs = [
      { source: 'wsrc_api', ref: '1', label: 'toy-api#1' },
      { source: 'wsrc_web', ref: '3', label: 'toy-web#3' },
    ] as ChunkSummary['work_refs'];
    const read = new Map([
      ['ch_1', { items: [{ source: 'wsrc_api', ref: '1', title: 'Rate-limit login' }, { source: 'wsrc_web', ref: '3', title: 'Drop moment.js' }] } as WorkItemsView],
    ]);
    expect(groups([chunk('ch_1', 'running', { work_refs: refs })], { workItems: read }).live[0]).toMatchObject({
      itemsRead: true,
      lines: [
        { label: 'toy-api#1', title: 'Rate-limit login', url: null },
        { label: 'toy-web#3', title: 'Drop moment.js', url: null },
      ],
    });
    expect(groups([chunk('ch_1', 'running', { work_refs: refs })]).live[0]).toMatchObject({
      itemsRead: false,
      lines: [{ label: 'toy-api#1', title: null }, { label: 'toy-web#3', title: null }],
    });
  });
});

describe('glanceVitals', () => {
  it('counts the needs and waiting lanes as needing you, the running lane as running, READY, and done today, beside the online fraction', () => {
    const list = groups([
      chunk('ch_needs', 'needs_human'),
      chunk('ch_wait', 'waiting_on_human'),
      chunk('ch_run', 'running'),
      chunk('ch_deliver', 'delivering'),
      chunk('ch_paused', 'paused'),
      chunk('ch_ready', 'ready'),
      chunk('ch_backlog', 'not_ready'),
      chunk('ch_done', 'done', { terminal: true, completed_at: ago(HOUR) }),
    ]);
    expect(glanceVitals([runner('r1', { online: true }), runner('r2', { online: false })], list)).toEqual({
      // needs_human, waiting_on_human, and paused, which waits in WAIT/HUMAN
      needsYou: 3,
      // running, and delivering, which shows under RUNNING
      running: 2,
      ready: 1,
      doneToday: 1,
      runnersUpLabel: '1/2',
    });
  });

  it('counts neither up nor total a runner that has never connected', () => {
    const fleet = [
      runner('r1', { online: true, connection: 'online' }),
      runner('r2', { online: false, connection: 'offline' }),
      runner('r3', { online: false, connection: 'never_connected' }),
    ];
    expect(glanceVitals(liveRunners(fleet), groups([])).runnersUpLabel).toBe('1/2');
  });
});

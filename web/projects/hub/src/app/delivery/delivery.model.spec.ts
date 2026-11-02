import { type ChunkFlowPlaceView, type ChunkSummary, type FlowFactView, type FlowView, type RepositorySummary, STATUS_TONE, toneColor, type WorkItemsView } from 'fleet';
import { bounceView, chunkSummary, FIXTURE_NOW, fixtureInstant, landedRepoView, prView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import {
  chunkLine,
  deliveryChunkIds,
  deliveryDetailIds,
  deliveryGraphIds,
  type DeliveryInputs,
  deliveryList,
  deliveryWhen,
  findRow,
  toggledSelection,
} from './delivery.model';

const MIN = 60;
const HOUR = 60 * MIN;

const chunk = (id: string, fields: Partial<ChunkSummary> = {}): ChunkSummary => chunkSummary({ chunk_id: id, graph_id: 'gr_1', status: 'running', ...fields });

const flow = (id: string, repositoryId: string): FlowView =>
  ({ flow_id: id, repository_id: repositoryId, branch: 'master', resource: `res-${id}`, line: [], last_landing: null, last_verdict: null, repository_name: null, repository_slug: null, turn: null }) as FlowView;

const repository = (id: string, name: string): RepositorySummary => ({ repository_id: id, name, owner: 'paul-gross', repo: name }) as RepositorySummary;

let factId = 0;
/** A flow fact `secondsAgo` before the fixture's now. */
const fact = (flowId: string, chunkId: string, kind: FlowFactView['kind'], secondsAgo: number, outcome: string | null = null): FlowFactView => ({
  id: ++factId,
  flow_id: flowId,
  chunk_id: chunkId,
  kind,
  at: fixtureInstant(-secondsAgo),
  outcome,
  revision: null,
  submission_id: `sub-${chunkId}`,
});

const place = (flowId: string, position: number, state: ChunkFlowPlaceView['state'] = 'waiting', fields: Partial<ChunkFlowPlaceView> = {}): ChunkFlowPlaceView => ({
  flow_id: flowId,
  position,
  state,
  branch: 'master',
  next_poll_at: null,
  repository_name: null,
  repository_slug: null,
  resource: `res-${flowId}`,
  ...fields,
});

const FLOWS = [flow('fl_a', 'rp_a'), flow('fl_b', 'rp_b')];
const REPOSITORIES = [repository('rp_a', 'blizzard'), repository('rp_b', 'blizzard-context')];

/** The page's reads over `chunks`, every flow's facts newest first. */
function inputs(chunks: readonly ChunkSummary[], fields: Partial<Omit<DeliveryInputs, 'facts'>> & { facts?: readonly FlowFactView[] } = {}): DeliveryInputs {
  const facts = new Map<string, FlowFactView[]>();
  for (const each of [...(fields.facts ?? [])].sort((a, b) => b.at.localeCompare(a.at))) facts.set(each.flow_id, [...(facts.get(each.flow_id) ?? []), each]);
  return {
    flows: FLOWS,
    bounces: new Map(),
    repositories: REPOSITORIES,
    workItems: new Map(),
    projects: [],
    graphNames: new Map(),
    runnerHosts: new Map(),
    now: FIXTURE_NOW,
    ...fields,
    chunks,
    facts,
  };
}

const delivering = (i: DeliveryInputs) => deliveryList(i).delivering;
const only = (i: DeliveryInputs) => {
  const rows = [...deliveryList(i).delivering, ...deliveryList(i).delivered];
  expect(rows).toHaveLength(1);
  return rows[0];
};

describe('the chunks the page reads', () => {
  const CHUNKS = [
    chunk('ch_pr', { open_prs: [prView()] }),
    chunk('ch_place', { flow_places: [place('fl_a', 1)] }),
    chunk('ch_merge', { status: 'waiting_on_human', awaiting_external_merge: true, graph_id: 'gr_2' }),
    chunk('ch_landed', { landed: true, status: 'done' }),
    chunk('ch_idle', { status: 'ready' }),
  ];

  it('reads titles for every chunk that can hold a row, and every running chunk', () => {
    const facts = new Map([['fl_a', [fact('fl_a', 'ch_bounced', 'turn-ended', MIN, 'failure')]]]);
    const chunks = [...CHUNKS, chunk('ch_run'), chunk('ch_bounced', { status: 'needs_human' })];
    expect(deliveryChunkIds(chunks, facts)).toEqual(['ch_pr', 'ch_place', 'ch_merge', 'ch_landed', 'ch_run', 'ch_bounced']);
  });

  it('reads the titles of every chunk the list draws a row for', () => {
    const chunks = [...CHUNKS, chunk('ch_bounced', { status: 'needs_human' })];
    const read = inputs(chunks, { facts: [fact('fl_a', 'ch_bounced', 'turn-ended', MIN, 'failure')] });
    const rows = [...deliveryList(read).delivering, ...deliveryList(read).delivered].map((row) => row.chunk.chunkId);
    expect(deliveryChunkIds(chunks, read.facts)).toEqual(expect.arrayContaining(rows));
  });

  it('reads the detail of only the unlanded chunks with a footprint', () => {
    expect(deliveryDetailIds(CHUNKS)).toEqual(['ch_pr', 'ch_place', 'ch_merge']);
  });

  it('reads the graph of every chunk', () => {
    expect(deliveryGraphIds(CHUNKS.slice(1, 3))).toEqual(['gr_1', 'gr_2']);
  });
});

describe('a row\'s stage', () => {
  it('reads an open PR, or an awaited merge, as PR open', () => {
    expect(only(inputs([chunk('ch_1', { open_prs: [prView()] })])).stage).toBe(0);
    expect(only(inputs([chunk('ch_1', { awaiting_external_merge: true })])).stage).toBe(0);
  });

  it('reads a place on any flow as in line, and a landing place as landing', () => {
    expect(only(inputs([chunk('ch_1', { flow_places: [place('fl_a', 2)] })])).stage).toBe(1);
    expect(only(inputs([chunk('ch_1', { flow_places: [place('fl_a', 2), place('fl_b', 0, 'landing')] })])).stage).toBe(2);
  });

  it('reads a chunk landed on any repository as landed, still delivering while unlanded overall', () => {
    const partial = only(inputs([chunk('ch_1', { landed_repos: [landedRepoView()], flow_places: [place('fl_b', 1)] })]));
    expect(partial.stage).toBe(3);
    expect(deliveryList(inputs([chunk('ch_1', { landed_repos: [landedRepoView()] })])).delivering).toHaveLength(1);
  });

  it('lists no row for a chunk with no footprint and no bounce', () => {
    expect(deliveryList(inputs([chunk('ch_1')]))).toEqual({ delivering: [], delivered: [] });
  });
});

describe('a row\'s bounce', () => {
  const ENVELOPE = JSON.stringify({ detail: 'pre-push checks failed', step: { name: 'lint', exit_code: 1 } });

  it('reads the chunk\'s own latest bounce, with its reason, count and age, while it holds no place', () => {
    const bounces = [bounceView({ cause: 'failure', envelope: ENVELOPE, recorded_at: fixtureInstant(-50 * MIN) }), bounceView({ cause: 'conflict', recorded_at: fixtureInstant(-3 * HOUR) })];
    const row = only(inputs([chunk('ch_1', { open_prs: [prView()] })], { bounces: new Map([['ch_1', bounces]]) }));
    expect(row.bounce).toMatchObject({ cause: 'failure', reason: 'pre-push checks failed', count: 2, age: '[50m 0s]', at: fixtureInstant(-50 * MIN) });
    expect(row.bounce?.title.split('\n')[0]).toBe('pre-push checks failed');
    expect(row.statusColor).toBe(toneColor('stale'));
  });

  it('drops the bounce once the chunk is back in line — resubmitted, the bounce is history', () => {
    const row = only(inputs([chunk('ch_1', { flow_places: [place('fl_a', 1)] })], { bounces: new Map([['ch_1', [bounceView()]]]) }));
    expect(row.bounce).toBeNull();
    expect(row.statusColor).toBe(toneColor(STATUS_TONE('running')));
  });

  it('falls back to the flow record\'s recent bounce while the chunk\'s own record is unread', () => {
    const row = only(inputs([chunk('ch_1')], { facts: [fact('fl_a', 'ch_1', 'turn-started', 20 * MIN), fact('fl_a', 'ch_1', 'turn-ended', 10 * MIN, 'failure')] }));
    expect(row.bounce).toMatchObject({ cause: 'failure', reason: null, count: 1, age: '[10m 0s]' });
    expect(row.stage).toBe(-1);
  });

  it('reads no flow bounce older than a day, on a flow the chunk is back in line on, or after a landing turn', () => {
    expect(delivering(inputs([chunk('ch_1')], { facts: [fact('fl_a', 'ch_1', 'turn-ended', 25 * HOUR, 'failure')] }))).toEqual([]);
    const placed = only(inputs([chunk('ch_1', { flow_places: [place('fl_a', 1)] })], { facts: [fact('fl_a', 'ch_1', 'turn-ended', MIN, 'failure')] }));
    expect(placed.bounce).toBeNull();
    expect(delivering(inputs([chunk('ch_1')], { facts: [fact('fl_a', 'ch_1', 'turn-ended', 2 * MIN, 'failure'), fact('fl_a', 'ch_1', 'turn-ended', MIN, 'landed')] }))).toEqual([]);
  });

  it('skips a pending poll to the turn that settled before it', () => {
    const row = only(inputs([chunk('ch_1')], { facts: [fact('fl_a', 'ch_1', 'turn-ended', 2 * MIN, 'conflict'), fact('fl_a', 'ch_1', 'turn-ended', MIN, 'pending')] }));
    expect(row.bounce?.cause).toBe('conflict');
  });

  it('prefers a later flow bounce over the chunk\'s own, counting it on top', () => {
    const own = new Map([['ch_1', [bounceView({ cause: 'failure', recorded_at: fixtureInstant(-2 * HOUR) })]]]);
    const row = only(inputs([chunk('ch_1', { open_prs: [prView()] })], { bounces: own, facts: [fact('fl_a', 'ch_1', 'turn-ended', 5 * MIN, 'conflict')] }));
    expect(row.bounce).toMatchObject({ cause: 'conflict', count: 2, age: '[5m 0s]' });
    expect(row.bounce?.title).toContain('No bounce recorded on the chunk for this turn');
  });

  it('keeps the chunk\'s own bounce over an earlier flow bounce', () => {
    const own = new Map([['ch_1', [bounceView({ cause: 'failure', recorded_at: fixtureInstant(-MIN) })]]]);
    const row = only(inputs([chunk('ch_1', { open_prs: [prView()] })], { bounces: own, facts: [fact('fl_a', 'ch_1', 'turn-ended', HOUR, 'conflict')] }));
    expect(row.bounce).toMatchObject({ cause: 'failure', count: 1 });
  });
});

describe('the list\'s order', () => {
  it('splits the delivering from the delivered, each newest first, an undated row last', () => {
    const chunks = [
      chunk('ch_old', { open_prs: [prView()] }),
      chunk('ch_undated', { open_prs: [prView()] }),
      chunk('ch_new', { flow_places: [place('fl_a', 1)] }),
      chunk('ch_land_old', { landed: true, status: 'done', completed_at: fixtureInstant(-5 * HOUR) }),
      chunk('ch_land_new', { landed: true, status: 'done', completed_at: fixtureInstant(-9 * HOUR) }),
    ];
    const list = deliveryList(
      inputs(chunks, {
        facts: [fact('fl_a', 'ch_old', 'submitted', 3 * HOUR), fact('fl_a', 'ch_new', 'submitted', HOUR), fact('fl_b', 'ch_land_new', 'landed', 2 * HOUR)],
      }),
    );
    expect(list.delivering.map((row) => row.chunk.chunkId)).toEqual(['ch_new', 'ch_old', 'ch_undated']);
    expect(list.delivered.map((row) => row.chunk.chunkId)).toEqual(['ch_land_new', 'ch_land_old']);
    expect(list.delivered[0].at).toBe(fixtureInstant(-2 * HOUR));
    expect(list.delivered[1].at).toBe(fixtureInstant(-5 * HOUR));
  });

  it('finds a listed row by chunk id across both runs, and nothing for none', () => {
    const list = deliveryList(inputs([chunk('ch_pr', { open_prs: [prView()] }), chunk('ch_done', { landed: true, status: 'done' })]));
    expect(findRow(list, 'ch_done')?.chunk.chunkId).toBe('ch_done');
    expect(findRow(list, 'ch_missing')).toBeNull();
    expect(findRow(list, null)).toBeNull();
  });

  it('toggles a pick off when the picked row is picked again', () => {
    expect(toggledSelection(null, 'ch_1')).toBe('ch_1');
    expect(toggledSelection('ch_2', 'ch_1')).toBe('ch_1');
    expect(toggledSelection('ch_1', 'ch_1')).toBeNull();
  });
});

describe('a row\'s line, links, step and icon', () => {
  it('names the best place, and every flow\'s place by repository only when there are several', () => {
    const multi = chunk('ch_1', { flow_places: [place('fl_a', 2), place('fl_b', 1), place('fl_x', 0, 'landing')] });
    expect(chunkLine(multi, inputs([multi]))).toEqual({ best: '#1', perRepo: '1st on blizzard-context · 2nd on blizzard' });
    const single = chunk('ch_1', { flow_places: [place('fl_a', 3)] });
    expect(chunkLine(single, inputs([single]))).toEqual({ best: '#3', perRepo: null });
  });

  it('holds no line while every place is landing, awaiting a merge or passed over', () => {
    const off = chunk('ch_1', { flow_places: [place('fl_a', 0, 'landing'), place('fl_b', 1, 'awaiting-merge'), place('fl_a', 2, 'passed-over')] });
    expect(chunkLine(off, inputs([off]))).toBeNull();
  });

  it('falls back to the place\'s own repository name for a flow it does not know', () => {
    const unknown = chunk('ch_1', { flow_places: [place('fl_a', 1), place('fl_z', 2, 'waiting', { repository_name: 'toy-api' })] });
    expect(chunkLine(unknown, inputs([unknown]))?.perRepo).toBe('1st on blizzard · 2nd on toy-api');
  });

  it('links an in-flight row\'s open PRs, and a landed row\'s commits then its merged PRs', () => {
    const open = only(inputs([chunk('ch_1', { open_prs: [prView({ number: 7 })] })]));
    expect(open.links).toEqual([{ repo: 'blizzard', text: '#7', url: prView().url, title: 'paul-gross/blizzard#7' }]);
    const landed = only(inputs([chunk('ch_1', { landed: true, status: 'done', landed_repos: [landedRepoView()], closed_prs: [prView({ number: 8 })] })]));
    expect(landed.links.map((link) => link.text)).toEqual(['9f2c4e1', '#8']);
  });

  it('writes the step as graph/node · host/runner, the runner only while one holds it', () => {
    const held = chunk('ch_1', { open_prs: [prView()], current_node_name: 'deliver', runner_id: 'rn_1', runner_name: 'r-claude' });
    expect(only(inputs([held], { graphNames: new Map([['gr_1', 'adv-dwf']]), runnerHosts: new Map([['rn_1', 'xps']]) })).step).toBe('adv-dwf/deliver · xps/r-claude');
    expect(only(inputs([chunk('ch_1', { open_prs: [prView()], current_node_name: 'deliver' })])).step).toBe('deliver');
    expect(only(inputs([chunk('ch_1', { open_prs: [prView()] })])).step).toBeNull();
  });

  it('keeps a human-waiting chunk\'s own icon, draws any other in flight as running, and a landed one as done', () => {
    expect(only(inputs([chunk('ch_1', { status: 'needs_human', open_prs: [prView()] })])).icon).toEqual({ lane: 'needs', status: 'needs_human' });
    expect(only(inputs([chunk('ch_1', { status: 'delivering', open_prs: [prView()] })])).icon).toEqual({ lane: 'running', status: 'running' });
    expect(only(inputs([chunk('ch_1', { landed: true, status: 'done' })])).icon).toEqual({ lane: 'done', status: 'done' });
  });

  it('holds a title skeleton until the chunk\'s work items are read', () => {
    const pr = chunk('ch_1', { open_prs: [prView()] });
    expect(only(inputs([pr])).chunk.titlesPending).toBe(true);
    expect(only(inputs([pr], { workItems: new Map([['ch_1', { items: [] } as unknown as WorkItemsView]]) })).chunk.titlesPending).toBe(false);
  });
});

describe('deliveryWhen', () => {
  const now = new Date(2026, 0, 15, 12, 0).getTime();
  const at = (...parts: [number, number, number, number]) => new Date(2026, 0, parts[0], parts[1], parts[2], parts[3]).toISOString();

  it('draws a same-day instant by its time alone', () => {
    expect(deliveryWhen(at(15, 9, 30, 0), now)).toMatchObject({ day: null, time: '09:30' });
  });

  it('heads yesterday\'s instant Yesterday, and an older one with its date', () => {
    expect(deliveryWhen(at(14, 23, 52, 0), now)).toMatchObject({ day: 'Yesterday', time: '23:52' });
    expect(deliveryWhen(at(10, 8, 5, 0), now)).toMatchObject({ day: '2026/01/10', time: '08:05' });
  });

  it('draws nothing for no instant, or an unreadable one', () => {
    expect(deliveryWhen(null, now)).toBeNull();
    expect(deliveryWhen('not a date', now)).toBeNull();
  });
});

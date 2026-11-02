import { type ChunkFlowPlaceView, type ChunkSummary, type FlowFactView, type FlowView, type RepositorySummary, STATUS_TONE, toneColor, type WorkItemsView } from 'fleet';
import { bounceView, chunkSummary, FIXTURE_NOW, fixtureInstant, landedRepoView, prView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import {
  cardStageColor,
  cardStageLabel,
  chunkLine,
  deliveredRecently,
  deliveryChunkIds,
  type DeliveryInputs,
  deliveryList,
  deliveryState,
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

/** The verdict the hub sends a `turn-ended` fact for these fixtures' outcomes: `landed`, none for a wait, else `bounced`. */
const verdictOf = (kind: FlowFactView['kind'], outcome: string | null): FlowFactView['verdict'] =>
  kind !== 'turn-ended' || outcome === null || outcome === 'pending' ? null : outcome === 'landed' ? 'landed' : 'bounced';

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
  verdict: verdictOf(kind, outcome),
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
    repositories: REPOSITORIES,
    workItems: new Map(),
    projects: [],
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
    const facts = new Map([['fl_a', [fact('fl_a', 'ch_onflow', 'turn-ended', MIN, 'failure')]]]);
    const chunks = [...CHUNKS, chunk('ch_run'), chunk('ch_onflow', { status: 'needs_human' }), chunk('ch_bounced', { status: 'needs_human', latest_bounce: bounceView() })];
    expect(deliveryChunkIds(chunks, facts)).toEqual(['ch_pr', 'ch_place', 'ch_merge', 'ch_landed', 'ch_run', 'ch_onflow', 'ch_bounced']);
  });

  it('reads the titles of every chunk the list draws a row for', () => {
    const chunks = [...CHUNKS, chunk('ch_bounced', { status: 'needs_human', latest_bounce: bounceView({ recorded_at: fixtureInstant(-MIN) }), bounce_count: 1 })];
    const read = inputs(chunks);
    const rows = [...deliveryList(read).delivering, ...deliveryList(read).delivered].map((row) => row.chunk.chunkId);
    expect(rows).toContain('ch_bounced');
    expect(deliveryChunkIds(chunks, read.facts)).toEqual(expect.arrayContaining(rows));
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

  it('reads a turn the hub judged landed as landed, though it left no landed repository', () => {
    const row = only(inputs([chunk('ch_1', { open_prs: [prView()] })], { facts: [fact('fl_a', 'ch_1', 'turn-ended', MIN, 'landed')] }));
    expect(row.stage).toBe(3);
  });

  it('lists no row for a chunk with no footprint and no bounce', () => {
    expect(deliveryList(inputs([chunk('ch_1')]))).toEqual({ delivering: [], delivered: [] });
  });
});

describe('a row\'s bounce', () => {
  const ENVELOPE = JSON.stringify({ cause: 'failure', detail: 'pre-push checks failed in `lint`', unlanded: [] });
  const bounced = (minutesAgo: number, fields: Partial<ChunkSummary> = {}) =>
    chunk('ch_1', { latest_bounce: bounceView({ cause: 'failure', envelope: ENVELOPE, recorded_at: fixtureInstant(-minutesAgo * MIN) }), bounce_count: 2, ...fields });

  it('reads the hub\'s latest bounce, its reason without code ticks, and the chunk\'s bounce count', () => {
    const row = only(inputs([bounced(50, { open_prs: [prView()] })]));
    expect(row.bounce).toMatchObject({ cause: 'failure', reason: 'pre-push checks failed in lint', count: 2, at: fixtureInstant(-50 * MIN) });
    expect(row.bounce?.title.split('\n')[0]).toBe('pre-push checks failed in lint');
    expect(row.statusColor).toBe(toneColor('stale'));
  });

  it('reads the bounce, not the turn that wrote it a moment later, as the row\'s one bounce', () => {
    const turnEnd = { ...fact('fl_a', 'ch_1', 'turn-ended', 0, 'failure'), at: new Date(Date.parse(fixtureInstant(-50 * MIN)) + 3).toISOString() };
    const row = only(inputs([bounced(50, { bounce_count: 1, open_prs: [prView()] })], { facts: [turnEnd] }));
    expect(row.bounce).toMatchObject({ cause: 'failure', reason: 'pre-push checks failed in lint', count: 1 });
    expect(row.bounce?.title).not.toContain('No bounce recorded');
  });

  it('drops the bounce once the chunk is back in line — resubmitted, the bounce is history — or has landed', () => {
    const placed = only(inputs([bounced(5, { flow_places: [place('fl_a', 1)] })]));
    expect(placed.bounce).toBeNull();
    expect(placed.statusColor).toBe(toneColor(STATUS_TONE('running')));
    expect(only(inputs([bounced(5, { landed: true, status: 'done' })])).bounce).toBeNull();
  });

  it('keeps a footprintless chunk\'s bounce for a day, and one with an open change however old', () => {
    expect(only(inputs([bounced(23 * 60)])).stage).toBe(-1);
    expect(delivering(inputs([bounced(25 * 60)]))).toEqual([]);
    expect(only(inputs([bounced(25 * 60, { open_prs: [prView()] })])).bounce?.cause).toBe('failure');
  });

  it('names the bounce withdrawn when the record\'s next fact on that flow withdraws its turn\'s submission, by record order not instant', () => {
    const turn = fact('fl_a', 'ch_1', 'turn-ended', 50 * MIN, 'failure');
    const withdrawal = { ...fact('fl_a', 'ch_1', 'withdrawn', 50 * MIN, 'failure'), verdict: null, at: turn.at };
    const row = only(inputs([bounced(50, { open_prs: [prView()] })], { facts: [turn, withdrawal] }));
    expect(row.bounce?.withdrawn).toBe(true);
    expect(cardStageLabel(row)).toBe('failure · withdrawn');
    const other = { ...withdrawal, submission_id: 'sub-other' };
    expect(only(inputs([bounced(50, { open_prs: [prView()] })], { facts: [turn, other] })).bounce?.withdrawn).toBe(false);
    expect(only(inputs([bounced(50, { open_prs: [prView()] })], { facts: [turn] })).bounce?.withdrawn).toBe(false);
  });

  it('reads no bounce from a turn the hub judged bounced with no bounce recorded', () => {
    expect(delivering(inputs([chunk('ch_1')], { facts: [fact('fl_a', 'ch_1', 'turn-ended', MIN, 'failure')] }))).toEqual([]);
  });
});

describe('the list\'s read state', () => {
  const settled = { isPending: () => false, isError: () => false };
  const list = deliveryList(inputs([chunk('ch_1', { open_prs: [prView()] })]));
  const read = (size: number, pending: boolean) => ({ facts: new Map(Array.from({ length: size }, (_, i) => [`fl_${i}`, []])), pending });

  it('loads until the first of the flows\' records is read, and not again when another flow\'s record is', () => {
    expect(deliveryState([settled], read(0, true), list)).toBe('loading');
    expect(deliveryState([settled], read(1, true), list)).toBe('ready');
  });

  it('answers the reads\' own pending and error, and empty for a list with no row', () => {
    expect(deliveryState([{ isPending: () => true, isError: () => false }], read(1, false), list)).toBe('loading');
    expect(deliveryState([{ isPending: () => false, isError: () => true }], read(1, false), list)).toBe('error');
    expect(deliveryState([settled], read(0, false), { delivering: [], delivered: [] })).toBe('empty');
  });
});

describe('the phone\'s cards', () => {
  it('keeps the delivered rows within the board\'s done window', () => {
    const list = deliveryList(
      inputs([
        chunk('ch_new', { landed: true, status: 'done', completed_at: fixtureInstant(-2 * HOUR) }),
        chunk('ch_old', { landed: true, status: 'done', completed_at: fixtureInstant(-49 * HOUR) }),
      ]),
    );
    expect(deliveredRecently(list, FIXTURE_NOW).map((row) => row.chunk.chunkId)).toEqual(['ch_new']);
  });

  it('names a bounce, a place in line, or the furthest stage, and colours it', () => {
    const bounce = only(inputs([chunk('ch_1', { open_prs: [prView()], latest_bounce: bounceView({ cause: 'conflict', recorded_at: fixtureInstant(-MIN) }), bounce_count: 1 })]));
    const inLine = only(inputs([chunk('ch_1', { flow_places: [place('fl_a', 2)] })]));
    const landed = only(inputs([chunk('ch_1', { landed: true, status: 'done' })]));
    expect([bounce, inLine, landed].map(cardStageLabel)).toEqual(['conflict', 'in line #2', 'landed']);
    expect([bounce, inLine, landed].map(cardStageColor)).toEqual(['var(--red)', 'var(--cyan)', toneColor('done')]);
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
    const held = chunk('ch_1', { open_prs: [prView()], current_node_name: 'deliver', graph_name: 'adv-dwf', runner_id: 'rn_1', runner_name: 'r-claude' });
    expect(only(inputs([held], { runnerHosts: new Map([['rn_1', 'xps']]) })).step).toBe('adv-dwf/deliver · xps/r-claude');
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

import { type ChunkDetail, type ChunkFlowPlaceView, type FlowFactView, type FlowView, type RepositorySummary } from 'fleet';
import { assetArtifactView, bounceView, chunkDetail, FIXTURE_NOW, fixtureInstant, landedRepoView, prView, transitionView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { deliveryDrawerView, deliveryHistory, type DrawerStep, OUTPUT_TAIL_LINES, outputTail } from './delivery-history.model';

const MIN = 60;

const FLOWS = [
  { flow_id: 'fl_a', repository_id: 'rp_a' },
  { flow_id: 'fl_b', repository_id: 'rp_b' },
] as FlowView[];
const REPOSITORIES = [
  { repository_id: 'rp_a', owner: 'paul-gross', repo: 'blizzard', name: 'blizzard' },
  { repository_id: 'rp_b', owner: 'paul-gross', repo: 'blizzard-context', name: 'blizzard-context' },
] as RepositorySummary[];
const FORGES: ReadonlyMap<string, string> = new Map([
  ['fl_a', 'paul-gross/blizzard'],
  ['fl_b', 'paul-gross/blizzard-context'],
]);

/** The verdict the hub sends a `turn-ended` fact for these fixtures' outcomes: `landed`, none for a wait, else `bounced`. */
const verdictOf = (kind: FlowFactView['kind'], outcome: string | null | undefined): FlowFactView['verdict'] =>
  kind !== 'turn-ended' || !outcome || outcome === 'pending' ? null : outcome === 'landed' ? 'landed' : 'bounced';

let factId = 0;
/** A flow fact `minutesAgo` before the fixture's now, under submission `sub`. */
const fact = (flowId: string, kind: FlowFactView['kind'], minutesAgo: number, fields: Partial<FlowFactView> = {}): FlowFactView => ({
  id: ++factId,
  flow_id: flowId,
  chunk_id: 'ch_1',
  kind,
  at: fixtureInstant(-minutesAgo * MIN),
  outcome: null,
  revision: null,
  submission_id: 'sub_1',
  verdict: verdictOf(kind, fields.outcome),
  ...fields,
});

const place = (flowId: string, position: number, state: ChunkFlowPlaceView['state']): ChunkFlowPlaceView =>
  ({ flow_id: flowId, position, state, branch: 'master', next_poll_at: null, repository_name: null, repository_slug: null, resource: flowId }) as ChunkFlowPlaceView;

const detail = (fields: Partial<ChunkDetail>): ChunkDetail => chunkDetail({ chunk_id: 'ch_1', history: [], ...fields });
const texts = (d: ChunkDetail) => deliveryHistory(d, FORGES, FIXTURE_NOW).map((entry) => entry.text);

describe('deliveryHistory', () => {
  it('folds a run of polls into one entry, and a settled turn into its outcome, newest first', () => {
    const record = [
      fact('fl_a', 'submitted', 30),
      fact('fl_a', 'turn-started', 29),
      fact('fl_a', 'turn-ended', 28, { outcome: 'pending' }),
      fact('fl_a', 'turn-started', 20),
      fact('fl_a', 'turn-ended', 19, { outcome: 'pending' }),
      fact('fl_a', 'turn-started', 10),
      fact('fl_a', 'turn-ended', 9, { outcome: 'landed' }),
      fact('fl_a', 'landed', 9, { revision: '9f2c4e1a7b3d5860fedcba98' }),
    ];
    const history = deliveryHistory(detail({ flow_record: record }), FORGES, FIXTURE_NOW);
    expect(history.map((entry) => entry.text)).toEqual([
      'landed 9f2c4e1',
      expect.stringMatching(/^waiting on checks · 2 polls · \d\d:\d\d–\d\d:\d\d$/),
      'queued',
    ]);
    expect(history.map((entry) => entry.tone)).toEqual(['done', 'idle', null]);
    expect(history[1].at).toBe(fixtureInstant(-19 * MIN));
    expect(history.every((entry) => entry.repos === 'blizzard')).toBe(true);
  });

  it('names a single poll in the singular', () => {
    const record = [fact('fl_a', 'turn-started', 3), fact('fl_a', 'turn-ended', 2, { outcome: 'pending' })];
    expect(texts(detail({ flow_record: record }))[0]).toMatch(/^waiting on checks · 1 poll · /);
  });

  it('reads a turn still running as started, and one that failed as its outcome, a withdrawal that followed it joined on', () => {
    expect(texts(detail({ flow_record: [fact('fl_a', 'turn-started', 1)] }))).toEqual(['landing attempt started']);
    const withdrawn = [fact('fl_a', 'turn-started', 5), fact('fl_a', 'turn-ended', 4, { outcome: 'failure' }), fact('fl_a', 'withdrawn', 3, { outcome: 'failure' })];
    const history = deliveryHistory(detail({ flow_record: withdrawn }), FORGES, FIXTURE_NOW);
    expect(history.map((entry) => entry.text)).toEqual(['landing attempt failure · withdrawn']);
    expect(history[0].tone).toBe('needs');
    expect(texts(detail({ flow_record: [fact('fl_a', 'turn-ended', 4, { outcome: 'conflict' })] }))).toEqual(['landing attempt conflict']);
  });

  it('reads a withdrawal on its own with its reason, and drops one that followed a landing', () => {
    expect(texts(detail({ flow_record: [fact('fl_a', 'withdrawn', 2, { outcome: 'parked' })] }))).toEqual(['withdrawn (parked)']);
    expect(texts(detail({ flow_record: [fact('fl_a', 'withdrawn', 2)] }))).toEqual(['withdrawn']);
    expect(texts(detail({ flow_record: [fact('fl_a', 'withdrawn', 2, { outcome: 'landed' })] }))).toEqual([]);
  });

  it('merges the same entry said on several flows at once, naming every repository', () => {
    const history = deliveryHistory(detail({ flow_record: [fact('fl_a', 'submitted', 5), fact('fl_b', 'submitted', 5)] }), FORGES, FIXTURE_NOW);
    expect(history).toHaveLength(1);
    expect(history[0].repos).toBe('blizzard · blizzard-context');
  });

  it('stitches in the graph transitions just before and just after each delivery event, whatever the nodes are named', () => {
    const history = [
      transitionView({ epoch: 1, from_node_name: 'build', to_node_name: 'review', recorded_at: fixtureInstant(-40 * MIN) }),
      transitionView({ epoch: 2, from_node_name: 'review', to_node_name: 'ship', recorded_at: fixtureInstant(-30 * MIN) }),
      transitionView({ epoch: 3, from_node_name: 'ship', to_node_name: 'resolve-scope', choice_name: 'conflict', recorded_at: fixtureInstant(-20 * MIN) }),
      transitionView({ epoch: 4, from_node_name: 'resolve-scope', to_node_name: 'build', recorded_at: fixtureInstant(-10 * MIN) }),
    ];
    const entries = deliveryHistory(detail({ history, flow_record: [fact('fl_a', 'submitted', 25)] }), FORGES, FIXTURE_NOW);
    expect(entries.map((entry) => [entry.text, entry.transition, entry.tone])).toEqual([
      ['ship · conflict → resolve-scope', true, 'idle'],
      ['queued', false, null],
      ['review → ship', true, 'idle'],
    ]);
    expect(entries[0].move).toMatchObject({ from: 'ship', choice: 'conflict', to: 'resolve-scope' });
  });

  it('reads a bounce with its repositories, detail, failed step and log', () => {
    const envelope = JSON.stringify({
      cause: 'failure',
      detail: 'checks failed in `test`',
      step: { name: 'test', exit_code: 1, output_tail: Array.from({ length: OUTPUT_TAIL_LINES + 3 }, (_, i) => `line ${i}`).join('\n'), log_artifact: 'test-log' },
      unlanded: [{ repo: 'paul-gross/blizzard', branch: 'feat/x', commit: 'a'.repeat(40) }],
      action: { name: 'land', choice: 'failure', log_artifact: 'deliver-log' },
    });
    const artifacts = [
      assetArtifactView({ name: 'deliver-log', key: 'log-old', recorded_at: fixtureInstant(-50 * MIN) }),
      assetArtifactView({ name: 'deliver-log', key: 'log-at', recorded_at: fixtureInstant(-10 * MIN) }),
      assetArtifactView({ name: 'deliver-log', key: 'log-later', recorded_at: fixtureInstant(-MIN) }),
    ];
    const [entry] = deliveryHistory(detail({ bounces: [bounceView({ cause: 'failure', envelope, recorded_at: fixtureInstant(-10 * MIN) })], artifacts }), FORGES, FIXTURE_NOW);
    expect(entry).toMatchObject({ text: 'bounced · failure', tone: 'needs', repos: 'blizzard', detail: 'checks failed in test', logKey: 'log-at', age: '[10m 0s]' });
    expect(entry.step).toMatchObject({ name: 'test', exitCode: 1, long: true });
  });

  it('reads a bounce whose envelope is not JSON as carrying nothing but its cause', () => {
    const [entry] = deliveryHistory(detail({ bounces: [bounceView({ cause: 'conflict', envelope: '<html>' })] }), FORGES, FIXTURE_NOW);
    expect(entry).toMatchObject({ text: 'bounced · conflict', tone: 'needs', repos: null, detail: null, step: null, logKey: null });
  });
});

describe('deliveryDrawerView', () => {
  it('draws nothing while the detail is unread', () => {
    expect(deliveryDrawerView(undefined, FLOWS, REPOSITORIES, FIXTURE_NOW)).toBeNull();
  });

  it('draws a block per repository the chunk touches, each in its own state', () => {
    const view = deliveryDrawerView(
      detail({
        flow_record: [fact('fl_a', 'submitted', 30), fact('fl_b', 'submitted', 30)],
        flow_places: [place('fl_b', 2, 'waiting')],
        landed_repos: [landedRepoView({ repo: 'paul-gross/blizzard' })],
        open_prs: [prView({ repo: 'paul-gross/toy-api', number: 4 })],
      }),
      FLOWS,
      REPOSITORIES,
      FIXTURE_NOW,
    )!;
    expect(view.repos.map((repo) => [repo.name, repo.state, repo.tone])).toEqual([
      ['blizzard', 'landed', 'done'],
      ['blizzard-context', 'in line · 2nd', expect.any(String)],
      ['toy-api', 'PR open', 'idle'],
    ]);
    expect(view.repos[0].commit?.text).toBe('9f2c4e1');
    expect(view.repos[2].prs).toEqual([{ text: '#4', url: prView().url }]);
  });

  it('reads a repository whose last attempt bounced as bounced, and one with nothing as not submitted', () => {
    const view = deliveryDrawerView(
      detail({ flow_record: [fact('fl_a', 'turn-ended', 5, { outcome: 'conflict' })], closed_prs: [prView({ repo: 'paul-gross/blizzard-context' })] }),
      FLOWS,
      REPOSITORIES,
      FIXTURE_NOW,
    )!;
    expect(view.repos.map((repo) => repo.state)).toEqual(['bounced · conflict', 'not submitted']);
    expect(view.repos[0].lastAttempt).toMatchObject({ outcome: 'conflict', age: '[5m 0s]' });
  });

  it('sums the attempts and bounces, and spans the delivery from first submission to landing once every repository has landed', () => {
    const record = [fact('fl_a', 'submitted', 120), fact('fl_a', 'submitted', 60, { submission_id: 'sub_2' }), fact('fl_a', 'landed', 7)];
    const landed = deliveryDrawerView(detail({ flow_record: record, landed_repos: [landedRepoView()], bounces: [bounceView()] }), FLOWS, REPOSITORIES, FIXTURE_NOW)!;
    expect(landed.summary).toEqual({ attempts: 2, bounces: 1, span: '[1h 53m]', spanLanded: true, reposLanded: 1, reposTotal: 1 });

    const open = deliveryDrawerView(detail({ flow_record: record.slice(0, 2) }), FLOWS, REPOSITORIES, FIXTURE_NOW)!;
    expect(open.summary).toMatchObject({ span: '[2h 0m]', spanLanded: false, reposLanded: 0 });
    expect(deliveryDrawerView(detail({}), FLOWS, REPOSITORIES, FIXTURE_NOW)!.summary.span).toBeNull();
  });
});

describe('outputTail', () => {
  const lines = (n: number) => Array.from({ length: n }, (_, i) => `l${i}`).join('\n');
  const step = (tail: string | null, long: boolean): DrawerStep => ({ name: 'test', exitCode: 1, outputTail: tail, long });

  it('shows a long tail\'s last lines folded, and all of it unfolded', () => {
    const tail = lines(OUTPUT_TAIL_LINES + 5);
    expect(outputTail(step(tail, true), false).split('\n')).toEqual(tail.split('\n').slice(-OUTPUT_TAIL_LINES));
    expect(outputTail(step(tail, true), true)).toBe(tail);
  });

  it('shows a short tail whole, and nothing for none', () => {
    expect(outputTail(step(lines(3), false), false)).toBe(lines(3));
    expect(outputTail(step(null, false), false)).toBe('');
  });
});

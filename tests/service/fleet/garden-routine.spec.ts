import { parse } from 'yaml';
import { expect, stepUntil, test } from '../../harness/fixtures';

const ROUTINE = 'garden-e2e';
const FOUND = 'garden-found';
const CLEAN = 'garden-clean';
const THICKET = 'garden-thicket';
const VIRGIN = 'garden-virgin';
const UNDECLARED = 'garden-undeclared-axis';
const UNKNOWN_ID = `fin_0${'Z'.repeat(25)}`;

type Node = { executor: string; action?: string; prompt?: string; judgement: {
  prompt?: string; choices: Record<string, { prompt_addendum?: string }>;
} };
type Graph = { name: string; nodes: Record<string, Node> };
type Finding = { finding_id: string; class: string; locus: string; live: boolean; observed_count: number };
type Proposal = { class: string; routine_name: string; findings: string[] };

function scriptedGraph(body: Graph, repo: string): string {
  const graph = structuredClone(body);
  const common = String.raw`import json, os, pathlib, subprocess
chunk_id = os.environ['BLIZZARD_CHUNK_ID']
def sh(*args, inp=None):
    return subprocess.run(list(args), input=inp, check=True, capture_output=True, text=True).stdout
item = json.loads(sh('blizzard', 'runner', 'work-items', chunk_id))['items'][0]
path = next(l.split('=', 1)[1] for l in item['body'].splitlines() if l.startswith('path='))
scope = next(l for l in item['body'].splitlines() if l.startswith('Scope:')).split()[1]
def publish(name, content):
    sh('blizzard', 'runner', 'artifact', 'create', '--name', name, inp=content)
def rows():
    return json.loads(sh('blizzard', 'runner', 'garden', 'findings'))
def bucket():
    return [r for r in rows() if r['scope_slug'] == scope and r['live']]
addendum_marker = pathlib.Path(f'.garden-addendum-{chunk_id}')
`;
  const survey = String.raw`rev = sh('git', '-C', '${repo}', 'rev-parse', 'HEAD').strip()
fmt = sh('blizzard', 'runner', 'artifact', 'get', '--scope', 'system', 'garden/finding-format', '--content')
assert 'FindingDelta' in fmt
if path in ('found', 'invalid', 'virgin'):
    cands = [
        {'ref': 'F1', 'class': 'stale-docstring', 'locus': 'src/app.py:1', 'summary': 'first weed'},
        {'ref': 'F2', 'class': 'stale-docstring', 'locus': 'src/app.py:2', 'summary': 'second weed'},
    ]
elif path == 'excessive':
    cands = [{'ref': 'F1', 'class': 'excessive-scope', 'locus': scope,
              'summary': 'roughly four hundred instances across the scope'}]
elif path == 'no-strategy':
    cands = [{'ref': 'F1', 'class': 'undeclared-axis', 'locus': 'gardening-axes registry',
              'summary': 'the routine axis has no declared registry entry'}]
else:
    cands = []
measurement = f'e2e sweep of {scope}: {len(cands)} flagged'
publish('survey', json.dumps({'scope': scope, 'revisions': {'${repo}': rev},
                              'measurement': measurement, 'candidates': cands}))
publish('delta', json.dumps({'scope': scope, 'revisions': {'${repo}': rev},
                             'measurement': measurement, 'findings': []}))
`;
  const reconcile = String.raw`envelope = json.loads(sh('blizzard', 'runner', 'artifact', 'get', 'survey', '--content'))
live = bucket()
if path == 'virgin':
    assert any(r['scope_slug'] == '${FOUND}' for r in rows()), 'neighbour findings not shown'
if path == 'invalid' and not addendum_marker.exists():
    delta = {'scope': scope, 'revisions': envelope['revisions'], 'measurement': envelope['measurement'],
             'findings': [{'op': 'observed', 'id': '${UNKNOWN_ID}'}]}
else:
    ops = []
    for cand in envelope['candidates']:
        match = next((f for f in live if f['class'] == cand['class'] and f['locus'] == cand['locus']), None)
        if match:
            ops.append({'op': 'observed', 'id': match['finding_id']})
        else:
            ops.append({'op': 'add', 'class': cand['class'], 'locus': cand['locus'],
                        'summary': cand['summary'], 'ref': cand['ref']})
    delta = {'scope': scope, 'revisions': envelope['revisions'], 'measurement': envelope['measurement'],
             'findings': ops}
publish('delta', json.dumps(delta))
`;
  const propose = String.raw`fmt = sh('blizzard', 'runner', 'artifact', 'get', '--scope', 'system', 'garden/proposal-format', '--content')
assert 'GardenProposalCandidate' in fmt
live = bucket()
if path == 'virgin':
    delta = json.loads(sh('blizzard', 'runner', 'artifact', 'get', 'delta', '--node', 'reconcile', '--content'))
    own_refs = [op['ref'] for op in delta['findings'] if op['op'] == 'add']
    docket = [{'ref': 'P1', 'class': 'handoff', 'title': f'hand {scope} out of the fleet',
               'body': 'scripted proposal citing the refs this very run minted', 'findings': own_refs}]
elif path == 'invalid' or not live:
    docket = []
else:
    docket = [{'ref': 'P1', 'class': 'handoff', 'title': f'hand {scope} out of the fleet',
               'body': 'scripted proposal citing every live finding in the bucket',
               'findings': [f['finding_id'] for f in live]}]
publish('docket', json.dumps(docket))
`;
  graph.nodes.survey!.prompt = common + survey;
  graph.nodes.survey!.judgement.prompt = common + String.raw`choice = {'found': 'found', 'invalid': 'found', 'virgin': 'found', 'excessive': 'excessive',
          'no-strategy': 'no-strategy', 'clean': 'clean'}[path]
verdict(choice, 'scripted sweep')
`;
  graph.nodes.reconcile!.prompt = common + reconcile;
  graph.nodes.reconcile!.judgement.prompt = common + "verdict('nothing-to-propose' if addendum_marker.exists() else 'converged', 'scripted match')\n";
  graph.nodes.propose!.prompt = common + propose;
  graph.nodes.propose!.judgement.prompt = common + String.raw`verdict('proposed' if (path == 'virgin' or (path != 'invalid' and bucket())) else 'none',
        'scripted propose')
`;
  graph.nodes.deliver!.judgement.choices.invalid!.prompt_addendum = String.raw`failure = sh('blizzard', 'runner', 'artifact', 'get', 'garden-delivery-failure', '--content')
assert 'fin_' in failure, f'failure artifact does not name the rejected id: {failure!r}'
good = {'scope': scope, 'revisions': envelope['revisions'], 'measurement': envelope['measurement'],
        'findings': [{'op': 'observed', 'id': f['finding_id']} for f in live]}
publish('delta', json.dumps(good))
addendum_marker.write_text('threaded')
`;
  expect(graph.nodes.deliver!.action).toBe('garden-deliver');
  expect(graph.name).toBe(body.name);
  expect(graph.nodes.deliver).toEqual({ ...body.nodes.deliver,
    judgement: { ...body.nodes.deliver!.judgement, choices: {
      ...body.nodes.deliver!.judgement.choices,
      invalid: { ...body.nodes.deliver!.judgement.choices.invalid, prompt_addendum: graph.nodes.deliver!.judgement.choices.invalid!.prompt_addendum },
    } },
  });
  return JSON.stringify(graph);
}

test.use({ isolation: 'fresh-tenant' });

test('test_garden_routine_runs_end_to_end_on_all_six_paths', async ({ api, repository, runnerHosts }) => {
  test.setTimeout(240000);
  const target = await repository();
  const listed = await api.get<{ graph_id: string; name: string }[]>('/api/graphs');
  expect(listed.status).toBe(200);
  const packaged = listed.body.find((item) => item.name === 'garden-routine');
  expect(packaged).toBeDefined();
  const authoring = await api.get<{ definition_yaml: string }>(`/api/graphs/${packaged!.graph_id}`);
  expect(authoring.status).toBe(200);
  const minted = await api.post('/api/graphs', { data: { definition_yaml: scriptedGraph(parse(authoring.body.definition_yaml) as Graph, target.name) } });
  expect(minted.status).toBe(201);
  const created = await api.post<{ routine_id: string }>('/api/routines', { data: {
    name: ROUTINE, graph_name: 'garden-routine', default_scope_slug: FOUND, default_model: [], default_effort: null,
  } });
  expect(created.status).toBe(201);
  const routineId = created.body.routine_id;
  for (const slug of [CLEAN, THICKET, VIRGIN, UNDECLARED]) {
    expect((await api.post('/api/scopes', { data: { slug, description: '' } })).status).toBe(201);
    expect((await api.put(`/api/routines/${routineId}/scopes/${slug}`)).status).toBe(204);
  }
  const host = await runnerHosts.create({ runnerConfig: { workspace: [{
    id: 'workspace-local', root: '${workspace}', provider: 'basic', base_branch: 'main',
    repo: [{ name: target.name, url: target.origin }],
  }] } });
  const run = async (path: string, scope: string, expected: [string, string][]): Promise<string> => {
    const response = await api.post<{ chunk_id: string }>(`/api/routines/${routineId}/run`, {
      data: { scope_slug: scope, mode: 'full', note: `path=${path}` },
    });
    expect(response.status).toBe(201);
    const id = response.body.chunk_id;
    expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
    const detail = await stepUntil(host, async () => (await api.get(`/api/chunks/${id}`)).body,
      (body) => body.status === 'done' || body.status === 'needs_human', 100);
    expect(detail.status).toBe('done');
    expect((detail.history as { from_node_name: string; choice_name: string }[])
      .map((edge) => [edge.from_node_name, edge.choice_name])).toEqual(expected);
    return id;
  };
  const findings = async (scope: string): Promise<Finding[]> => {
    const response = await api.get<{ findings: Finding[] }>('/api/findings', { params: { routine: ROUTINE, scope } });
    expect(response.status).toBe(200);
    return response.body.findings;
  };
  const proposals = async (): Promise<Proposal[]> => {
    const response = await api.get<{ proposals: Proposal[] }>('/api/garden-proposals');
    expect(response.status).toBe(200);
    return response.body.proposals;
  };
  const delivered = async (id: string) => {
    const response = await api.get<{ chunk_id: string; outcome: string; sets: {
      measurement: string; revisions: Record<string, string>;
      added: { class: string; locus: string; summary: string }[]; observed: unknown[]; gone: unknown[];
    }[] }>(`/api/runs/${id}`);
    expect(response.status).toBe(200);
    expect(response.body.chunk_id).toBe(id);
    expect(response.body.outcome).toBe('done');
    expect(response.body.sets).toHaveLength(1);
    return response.body.sets[0]!;
  };

  const since = new Date(Date.now() - 5 * 60_000).toISOString();
  const found = await run('found', FOUND, [['survey', 'found'], ['reconcile', 'converged'], ['propose', 'none'], ['deliver', 'recorded']]);
  const foundRows = await findings(FOUND);
  expect(foundRows.map((row) => [row.class, row.locus])).toEqual(expect.arrayContaining([
    ['stale-docstring', 'src/app.py:1'], ['stale-docstring', 'src/app.py:2'],
  ]));
  expect(foundRows).toHaveLength(2);
  expect(foundRows.every((row) => row.live)).toBe(true);
  expect(await proposals()).toEqual([]);
  const runs = await api.get<{ chunk_id: string; routine_name: string; scope_slug: string; mode: string;
    outcome: string; delivered: { finding_set_id: string }[] }[]>('/api/runs', {
    params: { since, until: new Date(Date.now() + 5 * 60_000).toISOString() },
  });
  expect(runs.status).toBe(200);
  const row = runs.body.find((item) => item.chunk_id === found);
  expect(row).toMatchObject({ routine_name: ROUTINE, scope_slug: FOUND, mode: 'full', outcome: 'done' });
  expect(row!.delivered.map((item) => item.finding_set_id)).not.toEqual([]);
  const foundSet = await delivered(found);
  expect(foundSet.added.map((item) => [item.class, item.locus, item.summary])).toEqual([
    ['stale-docstring', 'src/app.py:1', 'first weed'], ['stale-docstring', 'src/app.py:2', 'second weed'],
  ]);
  expect([foundSet.observed, foundSet.gone]).toEqual([[], []]);

  const clean = await run('clean', CLEAN, [['survey', 'clean'], ['deliver', 'recorded']]);
  expect(await findings(CLEAN)).toEqual([]);
  const cleanSet = await delivered(clean);
  expect(cleanSet.measurement).toBe(`e2e sweep of ${CLEAN}: 0 flagged`);
  expect(cleanSet.revisions[target.name]).toEqual(expect.any(String));
  expect([cleanSet.added, cleanSet.observed, cleanSet.gone]).toEqual([[], [], []]);

  const first = await run('excessive', THICKET, [['survey', 'excessive'], ['reconcile', 'converged'], ['propose', 'none'], ['deliver', 'recorded']]);
  const firstRows = await findings(THICKET);
  expect(firstRows).toHaveLength(1);
  expect(firstRows[0]!.class).toBe('excessive-scope');
  const bailoutId = firstRows[0]!.finding_id;
  const observedBefore = firstRows[0]!.observed_count;
  const second = await run('excessive', THICKET, [['survey', 'excessive'], ['reconcile', 'converged'], ['propose', 'proposed'], ['deliver', 'recorded']]);
  const thicketRows = await findings(THICKET);
  expect(thicketRows.map((item) => item.finding_id)).toEqual([bailoutId]);
  expect(thicketRows[0]!.observed_count).toBe(observedBefore + 1);
  expect(await proposals()).toEqual([expect.objectContaining({ class: 'handoff', routine_name: ROUTINE, findings: [bailoutId] })]);

  const before = new Map(foundRows.map((item) => [item.finding_id, item.observed_count]));
  const invalid = await run('invalid', FOUND, [['survey', 'found'], ['reconcile', 'converged'], ['propose', 'none'],
    ['deliver', 'invalid'], ['reconcile', 'nothing-to-propose'], ['deliver', 'recorded']]);
  const after = await findings(FOUND);
  expect(new Set(after.map((item) => item.finding_id))).toEqual(new Set(before.keys()));
  for (const item of after) expect(item.observed_count).toBe(before.get(item.finding_id)! + 1);
  expect(await proposals()).toHaveLength(1);

  const virgin = await run('virgin', VIRGIN, [['survey', 'found'], ['reconcile', 'converged'], ['propose', 'proposed'], ['deliver', 'recorded']]);
  const virginRows = await findings(VIRGIN);
  expect(new Set(virginRows.map((item) => `${item.class}:${item.locus}`))).toEqual(new Set([
    'stale-docstring:src/app.py:1', 'stale-docstring:src/app.py:2',
  ]));
  const virginIds = new Set(virginRows.map((item) => item.finding_id));
  const matching = (await proposals()).filter((item) => item.findings.length === virginIds.size &&
    item.findings.every((id) => virginIds.has(id)));
  expect(matching).toEqual([expect.objectContaining({ class: 'handoff', routine_name: ROUTINE })]);

  const undeclared = await run('no-strategy', UNDECLARED, [['survey', 'no-strategy'], ['reconcile', 'converged'],
    ['propose', 'none'], ['deliver', 'recorded']]);
  expect(await findings(UNDECLARED)).toEqual([expect.objectContaining({ class: 'undeclared-axis', live: true })]);

  const sessions = await host.mockSessions();
  for (const id of [found, first, second, invalid, virgin, undeclared]) {
    const leases = await host.runners['runner-local']!.leasesForChunk(id);
    expect(leases.status).toBe(200);
    const items = leases.body.items;
    expect(items.map((item) => item.created_at)).toEqual(items.map((item) => item.created_at).sort());
    const surveySession = items.find((item) => item.node_name === 'survey')!.session_id;
    const matches = items.filter((item) => item.node_name === 'reconcile').map((item) => item.session_id);
    const proposalSession = items.find((item) => item.node_name === 'propose')!.session_id;
    expect(surveySession).toEqual(expect.any(String));
    expect(matches[0]).toEqual(expect.any(String));
    expect(matches[0]).not.toBe(surveySession);
    expect(proposalSession).toBe(matches[0]);
    if (id === invalid) {
      expect(matches).toHaveLength(2);
      expect(matches[1]).not.toBe(matches[0]);
    } else expect(matches).toHaveLength(1);
    for (const sessionId of [surveySession, ...matches, proposalSession]) {
      expect(sessions.find((session) => session.session_id === sessionId)?.turns).toBeGreaterThanOrEqual(1);
    }
  }
});

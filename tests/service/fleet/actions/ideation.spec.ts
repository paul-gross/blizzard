import { parse } from 'yaml';
import { expect, stepUntil, test } from '../../../harness/fixtures';

const ROUTINE = 'ideation-e2e';
const scopes = {
  novel: 'ideation-novel', clean: 'ideation-clean', none: 'ideation-none',
  invalid: 'ideation-invalid', failure: 'ideation-failure', escalate: 'ideation-escalate',
  undeclared: 'ideation-undeclared', declared: 'ideation-declared-later',
};

type Node = { executor: string; action?: string; with?: unknown; prompt?: string; judgement: {
  prompt?: string; choices: Record<string, { prompt_addendum?: string }>;
} };
type Graph = { name: string; nodes: Record<string, Node> };
type Proposal = { proposal_id: string; title: string; body: string; class: string; findings: string[];
  routine_name: string; closure: { closure: string } | null };
type SetRow = { added: unknown[]; observed: unknown[]; gone: unknown[];
  revisions: Record<string, string>; measurement: string };

const common = String.raw`import json, os, pathlib, subprocess
chunk_id = os.environ['BLIZZARD_CHUNK_ID']
def sh(*args, inp=None):
    return subprocess.run(list(args), input=inp, check=True, capture_output=True, text=True).stdout
item = json.loads(sh('blizzard', 'runner', 'work-items', chunk_id))['items'][0]
lines = item['body'].splitlines()
scope = next(l for l in lines if l.startswith('Scope:')).split()[1]
path = next(l.split('=', 1)[1] for l in lines if l.startswith('path='))
def publish(name, content):
    sh('blizzard', 'runner', 'artifact', 'create', '--name', name, inp=content)
def staged(name):
    entries = json.loads(sh('blizzard', 'runner', 'artifact', 'staged', '--content'))
    return next(e['content'] for e in entries if e['name'] == name)
axis_marker = pathlib.Path(f'.ideation-axis-{chunk_id}')
`;

const survey = String.raw`fmt = sh('blizzard', 'runner', 'artifact', 'get', '--scope', 'system', 'garden/finding-format', '--content')
assert 'FindingDelta' in fmt
if path in ('ask-undeclared', 'ask-declared'):
    ask(f'no gardening-axes registry entry declares the ideation-e2e axis for {scope} — declare it or confirm its absence?',
        ['declared', 'still-undeclared'])
if path in ('novel', 'invalid', 'escalate', 'failure', 'reconcile-drop'):
    cands = [
        {'ref': 'C1', 'class': 'missing-capability', 'locus': f'{scope}: persona-a direction gap',
         'summary': 'persona A cannot do the core workflow at all'},
        {'ref': 'C2', 'class': 'partial-capability', 'locus': f'{scope}: persona-b tweak gap',
         'summary': 'persona B only gets this on one surface'},
        {'ref': 'C3', 'class': 'unwanted-capability', 'locus': f'{scope}: persona-c retire gap',
         'summary': 'a surface the charter no longer asks for'},
    ]
elif path == 'revive':
    cands = [{'ref': 'C2', 'class': 'partial-capability', 'locus': f'{scope}: persona-b tweak gap',
              'summary': 'changed since the pass: the parity gap is no longer intentional'}]
elif path == 'none':
    cands = [{'ref': 'C1', 'class': 'minor-gap', 'locus': f'{scope}: persona-d minor gap',
              'summary': 'a minor gap not worth a proposal'}]
else:
    cands = []
measurement = f'e2e ideation sweep of {scope}: {len(cands)} flagged, 0 proposed'
publish('survey', json.dumps({'scope': scope, 'revisions': {}, 'measurement': measurement, 'candidates': cands}))
publish('delta', json.dumps({'scope': scope, 'revisions': {}, 'measurement': measurement, 'findings': []}))
`;
const surveyJudgement = String.raw`if axis_marker.exists():
    choice = 'undeclared' if axis_marker.read_text().strip() == 'undeclared' else 'empty'
else:
    choice = {'novel': 'found', 'invalid': 'found', 'escalate': 'found', 'failure': 'found',
              'reconcile-drop': 'found', 'revive': 'found', 'none': 'found', 'clean': 'empty'}[path]
verdict(choice, 'scripted sweep')
`;
const reconcile = String.raw`survey_asset = json.loads(sh('blizzard', 'runner', 'artifact', 'get', 'survey', '--content'))
proposals = json.loads(sh('blizzard', 'runner', 'garden', 'proposals', '--state', 'all'))
def weigh(cand):
    for p in proposals:
        if cand['locus'] not in p['title']:
            continue
        closure = p.get('closure')
        if closure is None or closure['closure'] == 'accepted':
            return 'drop', None
        assert closure['closure'] == 'passed', closure
        assert closure['reason'], f'passed proposal {p["proposal_id"]} carried no reason to weigh'
        if cand['summary'].startswith('changed since the pass'):
            return 'revive', p
        return 'drop', None
    return 'keep', None
shortlist = []
for cand in survey_asset['candidates']:
    outcome, earlier = weigh(cand)
    if outcome == 'drop':
        continue
    entry = dict(cand)
    if outcome == 'revive':
        entry['revives'] = earlier['proposal_id']
        entry['changed'] = cand['summary']
    shortlist.append(entry)
publish('shortlist', json.dumps(shortlist))
`;
const propose = String.raw`fmt = sh('blizzard', 'runner', 'artifact', 'get', '--scope', 'system', 'garden/proposal-format', '--content')
assert 'GardenProposalCandidate' in fmt
classes_text = sh('blizzard', 'runner', 'artifact', 'get', 'classes', '--scope', 'graph', '--content')
assert 'direction' in classes_text
shortlist = json.loads(sh('blizzard', 'runner', 'artifact', 'get', 'shortlist', '--content'))
class_by_ref = {'C1': 'direction', 'C2': 'tweak', 'C3': 'retire'}
if path == 'none':
    docket = []
else:
    docket = [
        {'ref': f"P-{c['ref']}", 'class': class_by_ref.get(c['ref'], 'tweak'),
         'title': f"ideation proposal: {c['locus']}",
         'body': (f"revives {c['revives']} — {c['changed']}" if 'revives' in c
                  else f"scripted proposal citing no findings, addressing: {c['summary']}"),
         'findings': []}
        for c in shortlist
    ]
measurement = f'e2e ideation sweep of {scope}: {len(docket)} proposed'
delta_scope = ('not-' + scope) if path in ('invalid', 'escalate') else scope
publish('docket', json.dumps(docket))
publish('delta', json.dumps({'scope': delta_scope, 'revisions': {}, 'measurement': measurement, 'findings': []}))
`;
const invalidAddendum = String.raw`failure = sh('blizzard', 'runner', 'artifact', 'get', 'garden-delivery-failure', '--content')
assert 'scope' in failure, f'failure artifact does not name the rejected scope: {failure!r}'
history = json.loads(sh('blizzard', 'runner', 'chunk', 'history'))
invalid_count = sum(1 for r in history if r.get('kind') == 'transition' and r.get('from_node') == 'deliver' and r.get('choice') == 'invalid')
if invalid_count >= 2:
    ask(f'ideation delivery for {scope} bounced invalid twice — resolve the delta shape by hand?', ['retry', 'abandon'])
else:
    corrected_scope = ('not-' + scope) if path == 'escalate' else scope
    publish('docket', json.dumps(docket))
    publish('delta', json.dumps({'scope': corrected_scope, 'revisions': {}, 'measurement': measurement, 'findings': []}))
`;
const failureAddendum = String.raw`history = json.loads(sh('blizzard', 'runner', 'chunk', 'history'))
failure_count = sum(1 for r in history if r.get('kind') == 'transition' and r.get('from_node') == 'deliver' and r.get('choice') == 'failure')
assert failure_count >= 1, f'the failure addendum ran with no failure transition on record: {history}'
if failure_count >= 2:
    ask(f'ideation delivery for {scope} failed twice — repair the delivery path?', ['retry', 'abandon'])
assert json.loads(staged('docket')) == docket, 'the docket changed across the failure bounce'
publish('docket', json.dumps(docket))
publish('delta', json.dumps({'scope': scope, 'revisions': {}, 'measurement': measurement, 'findings': []}))
`;

function answerAxis(marker: string, tail: string): string {
  return String.raw`import json, os, pathlib, subprocess
chunk_id = os.environ['BLIZZARD_CHUNK_ID']
def sh(*args, inp=None):
    return subprocess.run(list(args), input=inp, check=True, capture_output=True, text=True).stdout
def publish(name, content):
    sh('blizzard', 'runner', 'artifact', 'create', '--name', name, inp=content)
item = json.loads(sh('blizzard', 'runner', 'work-items', chunk_id))['items'][0]
scope = next(l for l in item['body'].splitlines() if l.startswith('Scope:')).split()[1]
measurement = f'e2e ideation sweep of {scope}: ${tail}'
publish('survey', json.dumps({'scope': scope, 'revisions': {}, 'measurement': measurement, 'candidates': []}))
publish('delta', json.dumps({'scope': scope, 'revisions': {}, 'measurement': measurement, 'findings': []}))
pathlib.Path(f'.ideation-axis-{chunk_id}').write_text('${marker}')
`;
}
const answerEscalate = String.raw`import json, os, subprocess
chunk_id = os.environ['BLIZZARD_CHUNK_ID']
def sh(*args, inp=None):
    return subprocess.run(list(args), input=inp, check=True, capture_output=True, text=True).stdout
def publish(name, content):
    sh('blizzard', 'runner', 'artifact', 'create', '--name', name, inp=content)
item = json.loads(sh('blizzard', 'runner', 'work-items', chunk_id))['items'][0]
scope = next(l for l in item['body'].splitlines() if l.startswith('Scope:')).split()[1]
staged_entries = json.loads(sh('blizzard', 'runner', 'artifact', 'staged', '--content'))
docket = json.loads(next(e['content'] for e in staged_entries if e['name'] == 'docket'))
measurement = f'e2e ideation sweep of {scope}: {len(docket)} proposed'
publish('docket', json.dumps(docket))
publish('delta', json.dumps({'scope': scope, 'revisions': {}, 'measurement': measurement, 'findings': []}))
`;

function scriptedGraph(body: Graph, failing = false): string {
  const graph = structuredClone(body);
  graph.nodes.survey!.prompt = common + survey;
  graph.nodes.survey!.judgement.prompt = common + surveyJudgement;
  graph.nodes.reconcile!.prompt = common + reconcile;
  graph.nodes.reconcile!.judgement.prompt = common + "shortlist = json.loads(staged('shortlist'))\nverdict('novel' if shortlist else 'nothing-new', 'scripted match')\n";
  graph.nodes.propose!.prompt = common + propose;
  graph.nodes.propose!.judgement.prompt = common + "docket = json.loads(staged('docket'))\nverdict('proposed' if docket else 'none', 'scripted propose')\n";
  graph.nodes.deliver!.judgement.choices.invalid!.prompt_addendum = invalidAddendum;
  graph.nodes.deliver!.judgement.choices.failure!.prompt_addendum = failureAddendum;
  expect(body.nodes.deliver!.action).toBe('garden-deliver');
  expect(graph.name).toBe('ideation');
  if (failing) {
    graph.nodes.deliver!.action = 'test.sequence';
    graph.nodes.deliver!.with = { choices: ['failure', 'recorded'] };
  }
  return JSON.stringify(graph);
}

test.use({ isolation: 'fresh-tenant' });

test('test_ideation_runs_end_to_end_on_all_authored_paths', async ({ api, runnerHost }) => {
  test.setTimeout(240000);
  const listed = await api.get<{ graph_id: string; name: string }[]>('/api/graphs');
  expect(listed.status).toBe(200);
  const packaged = listed.body.find((item) => item.name === 'ideation');
  expect(packaged).toBeDefined();
  const authoring = await api.get<{ definition_yaml: string }>(`/api/graphs/${packaged!.graph_id}`);
  expect(authoring.status).toBe(200);
  const body = parse(authoring.body.definition_yaml) as Graph;
  const mint = async (failing = false) => {
    expect((await api.post('/api/graphs', { data: { definition_yaml: scriptedGraph(body, failing) } })).status).toBe(201);
  };
  await mint();
  const created = await api.post<{ routine_id: string }>('/api/routines', { data: {
    name: ROUTINE, graph_name: 'ideation', default_scope_slug: scopes.novel, default_model: [], default_effort: null,
  } });
  expect(created.status).toBe(201);
  const routineId = created.body.routine_id;
  for (const slug of [scopes.clean, scopes.none, scopes.invalid, scopes.failure, scopes.escalate, scopes.undeclared, scopes.declared]) {
    expect((await api.post('/api/scopes', { data: { slug, description: '' } })).status).toBe(201);
    expect((await api.put(`/api/routines/${routineId}/scopes/${slug}`)).status).toBe(204);
  }
  const edges = (detail: { history: { from_node_name: string; choice_name: string }[] }) =>
    detail.history.map((item) => [item.from_node_name, item.choice_name]);
  const start = async (path: string, scope: string): Promise<string> => {
    const response = await api.post<{ chunk_id: string }>(`/api/routines/${routineId}/run`, {
      data: { scope_slug: scope, mode: 'full', note: `path=${path}` },
    });
    expect(response.status).toBe(201);
    const id = response.body.chunk_id;
    expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
    return id;
  };
  const detailOf = async (id: string) => (await api.get<{ status: string; history: { from_node_name: string; choice_name: string }[];
    questions: { question_id: string; question: string }[] }>(`/api/chunks/${id}`)).body;
  const finish = async (id: string) => {
    const detail = await stepUntil(runnerHost, () => detailOf(id),
      (value) => ['done', 'needs_human', 'stopped'].includes(value.status), 120);
    expect(detail.status).toBe('done');
    return detail;
  };
  const run = async (path: string, scope: string, expected: string[][]) => {
    const id = await start(path, scope);
    expect(edges(await finish(id))).toEqual(expected);
    return id;
  };
  const viaAsk = async (path: string, scope: string, answer: string, questionText: string, expected: string[][]) => {
    const id = await start(path, scope);
    const parked = await stepUntil(runnerHost, () => detailOf(id),
      (value) => ['waiting_on_human', 'done', 'needs_human'].includes(value.status), 120);
    expect(parked.status).toBe('waiting_on_human');
    expect(parked.questions).toHaveLength(1);
    expect(parked.questions[0]!.question).toContain(questionText);
    expect((await api.post(`/api/questions/${parked.questions[0]!.question_id}/answers`, { data: { answer } })).status).toBe(201);
    expect(edges(await finish(id))).toEqual(expected);
    return id;
  };
  const proposals = async (): Promise<Proposal[]> => {
    const response = await api.get<{ proposals: Proposal[] }>('/api/garden-proposals');
    expect(response.status).toBe(200);
    return response.body.proposals.filter((item) => item.routine_name === ROUTINE);
  };
  const delivered = async (id: string): Promise<SetRow> => {
    const response = await api.get<{ chunk_id: string; outcome: string; sets: SetRow[] }>(`/api/runs/${id}`);
    expect(response.status).toBe(200);
    expect(response.body).toMatchObject({ chunk_id: id, outcome: 'done' });
    expect(response.body.sets).toHaveLength(1);
    return response.body.sets[0]!;
  };
  const found = await run('novel', scopes.novel, [['survey', 'found'], ['reconcile', 'novel'], ['propose', 'proposed'], ['deliver', 'recorded']]);
  expect(await delivered(found)).toMatchObject({ added: [], observed: [], gone: [], revisions: {},
    measurement: `e2e ideation sweep of ${scopes.novel}: 3 proposed` });
  const first = await proposals();
  expect(first).toHaveLength(3);
  expect(new Set(first.map((item) => item.class))).toEqual(new Set(['direction', 'tweak', 'retire']));
  expect(first.every((item) => item.findings.length === 0)).toBe(true);
  const direction = first.find((item) => item.class === 'direction')!;
  const tweak = first.find((item) => item.class === 'tweak')!;
  const accepted = await api.post<{ chunk_id: string | null; closure: { closure: string } }>(
    `/api/garden-proposals/${direction.proposal_id}/accept`, { data: { mint_work_item: false, reason: 'clear win, no ticket needed yet' } });
  expect(accepted.status).toBe(200);
  expect(accepted.body).toMatchObject({ chunk_id: null, closure: { closure: 'accepted' } });
  const passed = await api.post<{ closure: { closure: string } }>(`/api/garden-proposals/${tweak.proposal_id}/pass`, {
    data: { reason: 'not now — the surface parity gap is intentional this quarter' },
  });
  expect(passed.status).toBe(200);
  expect(passed.body.closure.closure).toBe('passed');

  const drop = await run('reconcile-drop', scopes.novel, [['survey', 'found'], ['reconcile', 'nothing-new'], ['deliver', 'recorded']]);
  expect(await proposals()).toHaveLength(3);
  expect((await delivered(drop)).measurement).toBe(`e2e ideation sweep of ${scopes.novel}: 3 flagged, 0 proposed`);
  await run('revive', scopes.novel, [['survey', 'found'], ['reconcile', 'novel'], ['propose', 'proposed'], ['deliver', 'recorded']]);
  const revived = await proposals();
  expect(revived).toHaveLength(4);
  const revival = revived.filter((item) => item.closure === null && item.class === 'tweak');
  expect(revival).toHaveLength(1);
  expect(revival[0]!.body).toContain(tweak.proposal_id);
  expect(revival[0]!.findings).toEqual([]);

  const clean = await run('clean', scopes.clean, [['survey', 'empty'], ['deliver', 'recorded']]);
  expect(await delivered(clean)).toMatchObject({ added: [], observed: [], gone: [], revisions: {},
    measurement: `e2e ideation sweep of ${scopes.clean}: 0 flagged, 0 proposed` });
  const none = await run('none', scopes.none, [['survey', 'found'], ['reconcile', 'novel'], ['propose', 'none'], ['deliver', 'recorded']]);
  expect(await delivered(none)).toMatchObject({ added: [], observed: [], gone: [],
    measurement: `e2e ideation sweep of ${scopes.none}: 0 proposed` });
  expect((await proposals()).filter((item) => item.title.includes(scopes.none))).toEqual([]);
  const invalid = await run('invalid', scopes.invalid, [['survey', 'found'], ['reconcile', 'novel'], ['propose', 'proposed'],
    ['deliver', 'invalid'], ['propose', 'proposed'], ['deliver', 'recorded']]);
  expect(await delivered(invalid)).toMatchObject({ added: [], observed: [], gone: [] });

  await mint(true);
  const failure = await start('failure', scopes.failure);
  await mint();
  expect(edges(await finish(failure))).toEqual([['survey', 'found'], ['reconcile', 'novel'], ['propose', 'proposed'],
    ['deliver', 'failure'], ['propose', 'proposed'], ['deliver', 'recorded']]);
  const escalate = await viaAsk('escalate', scopes.escalate, answerEscalate, 'bounced invalid twice', [
    ['survey', 'found'], ['reconcile', 'novel'], ['propose', 'proposed'], ['deliver', 'invalid'],
    ['propose', 'proposed'], ['deliver', 'invalid'], ['propose', 'proposed'], ['deliver', 'recorded'],
  ]);
  expect(edges(await detailOf(escalate)).filter(([node, choice]) => node === 'deliver' && choice === 'invalid')).toHaveLength(2);
  const undeclared = await viaAsk('ask-undeclared', scopes.undeclared,
    answerAxis('undeclared', 'axis still undeclared, nothing to sweep'), 'ideation-e2e axis', [['survey', 'undeclared']]);
  const noDelivery = await api.get<{ sets: SetRow[] }>(`/api/runs/${undeclared}`);
  expect(noDelivery.status).toBe(200);
  expect(noDelivery.body.sets).toEqual([]);
  expect((await proposals()).filter((item) => item.title.includes(scopes.undeclared))).toEqual([]);
  await viaAsk('ask-declared', scopes.declared,
    answerAxis('declared', '0 flagged after the registry was updated'), 'ideation-e2e axis',
    [['survey', 'empty'], ['deliver', 'recorded']]);

  const response = await runnerHost.runners['runner-local']!.leasesForChunk(found);
  expect(response.status).toBe(200);
  const leases = response.body.items;
  expect(leases.map((item) => item.created_at)).toEqual(leases.map((item) => item.created_at).sort());
  expect(leases.map((item) => item.node_name)).toEqual(['survey', 'reconcile', 'propose']);
  const [surveyLease, reconcileLease, proposeLease] = leases;
  expect(surveyLease!.session_id).toEqual(expect.any(String));
  expect(reconcileLease!.session_id).toEqual(expect.any(String));
  expect(reconcileLease!.session_id).not.toBe(surveyLease!.session_id);
  expect(proposeLease!.session_id).toBe(reconcileLease!.session_id);
});

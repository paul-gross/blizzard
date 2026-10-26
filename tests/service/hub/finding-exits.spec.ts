import * as fs from 'node:fs';
import { expect, test, type HubApi, type MockRunner, type Repository } from '../../harness/fixtures';

const routine = 'garden-service';
const scope = 'garden-svc';

interface Garden {
  api: HubApi;
  runner: MockRunner;
  repository: Repository;
  head: string;
  routineId: string;
  nodes: Record<string, string>;
}

async function garden(api: HubApi, runner: MockRunner, repository: Repository, head: string): Promise<Garden> {
  const graphs = await api.get('/api/graphs');
  expect(graphs.status).toBe(200);
  const packaged = graphs.body.find((graph: { name: string }) => graph.name === 'garden-routine');
  expect(packaged).toBeDefined();
  const graph = await api.get(`/api/graphs/${packaged.graph_id}`);
  expect(graph.status).toBe(200);
  const nodes = Object.fromEntries(graph.body.nodes.map((node: { name: string; node_id: string }) => [node.name, node.node_id]));
  const created = await api.post('/api/routines', { data: {
    name: routine, graph_name: 'garden-routine', default_scope_slug: scope, default_model: [], default_effort: null,
  } });
  expect(created.status).toBe(201);
  const registered = await runner.drive('register');
  expect(registered.body.status).toBe(201);
  return { api, runner, repository, head, routineId: created.body.routine_id, nodes };
}

async function deliver(g: Garden, ops: object[], options: { scope?: string; proposals?: object[]; routineId?: string } = {}) {
  const targetScope = options.scope ?? scope;
  const proposals = options.proposals ?? [];
  const run = await g.api.post(`/api/routines/${options.routineId ?? g.routineId}/run`, {
    data: { scope_slug: targetScope, mode: 'full', note: 'sweep' },
  });
  expect(run.status).toBe(201);
  const chunkId = run.body.chunk_id;
  expect((await g.api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
  const claimed = await g.runner.drive('claim', { chunk_id: chunkId });
  expect(claimed.body.claimed).toBe(true);
  const artifacts = [
    { name: 'survey', kind: 'asset', content: '{}', attached: true },
    { name: 'delta', kind: 'asset', content: JSON.stringify({
      scope: targetScope, revisions: { [g.repository.name]: g.head }, measurement: 'service sweep', findings: ops,
    }), attached: true },
    ...(proposals.length ? [{ name: 'docket', kind: 'asset', content: JSON.stringify(proposals), attached: true }] : []),
  ];
  const completed = await g.runner.drive('complete', { chunk_id: chunkId, choice: 'found', artifacts });
  expect(completed.body.response?.outcome).toBe('next');
  const detail = await g.api.get(`/api/chunks/${chunkId}`);
  expect(detail.status).toBe(200);
  return g.api.post(`/api/chunks/${chunkId}/garden-delivery`, {
    params: { node_id: g.nodes.deliver!, epoch: detail.body.latest_epoch },
    data: { delta: ['delta'], proposals: proposals.length ? ['docket'] : [] },
  });
}

function add_op(locus: string, introduced?: string) {
  return { op: 'add', class: 'stale-docstring', locus, summary: `weed at ${locus}`, ...(introduced ? { introduced } : {}) };
}

async function live(g: Garden) {
  const response = await g.api.get('/api/findings', { params: { routine, scope } });
  expect(response.status).toBe(200);
  return response.body.findings as { finding_id: string; locus: string; state: string }[];
}

async function seed(g: Garden, count: number, introduced?: string): Promise<string[]> {
  const response = await deliver(g, Array.from({ length: count }, (_, i) => add_op(`src/app.py:${i}`, introduced)));
  expect(response.status).toBe(200);
  expect(response.body.outcome).toBe('recorded');
  const rows = await live(g);
  expect(rows.length).toBeGreaterThanOrEqual(count);
  return rows.slice(0, count).map((row) => row.finding_id);
}

async function read_back(g: Garden, id: string) {
  const response = await g.api.get(`/api/findings/${id}`);
  expect(response.status).toBe(200);
  return response.body;
}

async function markMerged(g: Garden, chunkId: string) {
  const detail = await g.api.get(`/api/chunks/${chunkId}`);
  expect(detail.status).toBe(200);
  const graph = await g.api.get(`/api/graphs/${detail.body.graph_id}`);
  expect(graph.status).toBe(200);
  const marker = await g.api.post(`/api/chunks/${chunkId}/hub-markers`, {
    params: { node_id: graph.body.entry_node_id, epoch: 1 },
    data: { name: `merged/${g.repository.name}`, content: 'landed' },
  });
  expect(marker.status).toBe(200);
}

test.describe('delivery and tenant-wide findings', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('test_every_exit_verb_and_reopen_land_over_real_http', async ({ api, forge, repository, mockRunner }) => {
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    expect(commit.status).toBe(200);
    const g = await garden(api, await mockRunner('garden'), repo, commit.body.sha);
    const [a, b, c, d, e, f] = await seed(g, 6) as [string, string, string, string, string, string];
    for (const [path, id, state, note, by] of [
      ['resolve', a, 'resolved', 'the fix landed'],
      ['confirm-gone', b, 'gone-confirmed', 'no longer reproduces'],
      ['wont-fix', c, 'wont-fix', 'not worth standing'],
      ['not-a-finding', d, 'not-a-finding', 'misread the standard'],
      ['supersede', e, 'superseded', 'folded into f', f],
    ]) {
      const response = await api.post(`/api/findings/${path}`, { data: { finding_ids: [id], note, ...(by ? { superseded_by: by } : {}) } });
      expect(response.status).toBe(200);
      expect(response.body).toMatchObject([{ finding_id: id, state, live: false, note }]);
      expect((await read_back(g, id!)).state).toBe(state);
    }
    expect((await live(g)).map((row) => row.finding_id)).toEqual([f]);
    const reopened = await api.post('/api/findings/reopen', { data: { finding_ids: [a], note: 'it came back' } });
    expect(reopened.status).toBe(200);
    expect(reopened.body).toMatchObject([{ state: 'live', live: true, note: 'it came back' }]);
    expect((await live(g)).map((row) => row.finding_id).sort()).toEqual([a, f].sort());
  });

  test('test_every_exit_verb_and_reopen_land_through_the_real_cli', async ({ api, forge, repository, mockRunner, cli }) => {
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    expect(commit.status).toBe(200);
    const g = await garden(api, await mockRunner('garden'), repo, commit.body.sha);
    const [a, b, c, d, e, f] = await seed(g, 6) as [string, string, string, string, string, string];
    for (const [args, id, state] of [
      [['resolve', a, '--note', 'the fix landed'], a, 'resolved'],
      [['confirm-gone', b, '--note', 'no longer reproduces'], b, 'gone-confirmed'],
      [['wont-fix', c, '--note', 'not worth standing'], c, 'wont-fix'],
      [['not-a-finding', d, '--note', 'misread the standard'], d, 'not-a-finding'],
      [['supersede', e, '--by', f, '--note', 'folded into f'], e, 'superseded'],
    ] as [string[], string, string][]) {
      const out = await cli(['hub', 'finding', ...args, '--json']);
      expect(out.code).toBe(0);
      expect(JSON.parse(out.stdout)).toMatchObject([{ finding_id: id, state, live: false }]);
      expect((await read_back(g, id)).state).toBe(state);
    }
    const shown = await cli(['hub', 'finding', 'show', a]);
    expect(shown.code).toBe(0);
    expect(shown.stdout).toContain('resolved');
    expect(shown.stdout).toContain('the fix landed');
    const reopened = await cli(['hub', 'finding', 'reopen', a, '--note', 'it came back', '--json']);
    expect(reopened.code).toBe(0);
    expect(JSON.parse(reopened.stdout)).toMatchObject([{ state: 'live', live: true }]);
    const listed = await cli(['hub', 'finding', 'list', '--routine', routine, '--scope', scope, '--json']);
    expect(listed.code).toBe(0);
    expect(JSON.parse(listed.stdout).map((row: { finding_id: string }) => row.finding_id).sort()).toEqual([a, f].sort());
  });

  test('test_a_blank_note_is_refused_at_both_edges_and_a_bulk_exit_is_one_call', async ({ api, forge, repository, mockRunner, cli }) => {
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    expect(commit.status).toBe(200);
    const g = await garden(api, await mockRunner('garden'), repo, commit.body.sha);
    const [a, b, c] = await seed(g, 3) as [string, string, string];
    expect((await api.post('/api/findings/resolve', { data: { finding_ids: [a], note: '   ' } })).status).toBe(422);
    expect((await api.post('/api/findings/resolve', { data: { finding_ids: [a] } })).status).toBe(422);
    expect((await read_back(g, a)).state).toBe('live');
    const blank = await cli(['hub', 'finding', 'resolve', a, '--note', '  ']);
    expect(blank.code).toBe(1);
    expect((blank.stderr + blank.stdout).toLowerCase()).toContain('note');
    const missing = await cli(['hub', 'finding', 'resolve', a]);
    expect(missing.code).toBe(2);
    expect(missing.stderr).toContain('--note');
    expect((await read_back(g, a)).state).toBe('live');
    const bulk = await api.post('/api/findings/wont-fix', { data: { finding_ids: [a, b, c], note: 'one decision, three weeds' } });
    expect(bulk.status).toBe(200);
    expect(bulk.body.map((row: { finding_id: string }) => row.finding_id)).toEqual([a, b, c]);
    for (const row of bulk.body) expect(row).toMatchObject({ state: 'wont-fix', note: 'one decision, three weeds' });
    expect(await live(g)).toEqual([]);
  });

  test('test_an_exited_finding_is_no_longer_a_delta_target_but_a_gone_flagged_one_still_is', async ({ api, forge, repository, mockRunner }) => {
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    expect(commit.status).toBe(200);
    const g = await garden(api, await mockRunner('garden'), repo, commit.body.sha);
    const [exited, flagged] = await seed(g, 2) as [string, string];
    expect((await api.post('/api/findings/wont-fix', { data: { finding_ids: [exited], note: 'declined' } })).status).toBe(200);
    const gone = await deliver(g, [{ op: 'gone', id: flagged, note: 'did not reproduce this sweep' }]);
    expect(gone.body).toEqual({ outcome: 'recorded', detail: '' });
    expect((await read_back(g, flagged)).state).toBe('gone');
    const rejected = await deliver(g, [{ op: 'observed', id: exited }]);
    expect(rejected.status).toBe(200);
    expect(rejected.body.outcome).toBe('invalid');
    expect(rejected.body.detail).toContain(exited);
    expect((await read_back(g, exited)).state).toBe('wont-fix');
    const revived = await deliver(g, [{ op: 'observed', id: flagged }]);
    expect(revived.body.outcome).toBe('recorded');
    expect((await read_back(g, flagged)).state).toBe('live');
  });

  test('test_the_trend_is_served_over_http_and_through_the_real_cli', async ({ api, forge, repository, mockRunner, cli }) => {
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    expect(commit.status).toBe(200);
    const g = await garden(api, await mockRunner('garden'), repo, commit.body.sha);
    const t0 = new Date();
    const shift = (days: number) => new Date(t0.getTime() + days * 86400000);
    const local = (date: Date) => [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-')
      + `T${[date.getHours(), date.getMinutes(), date.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':')}`;
    const recorded = await deliver(g, [add_op('src/app.py:1', g.head), add_op('src/app.py:2', g.head), add_op('src/app.py:3')]);
    expect(recorded.body.outcome).toBe('recorded');
    const ids = Object.fromEntries((await live(g)).map((row) => [row.locus, row.finding_id]));
    for (const [verb, locus, note] of [
      ['resolve', 'src/app.py:1', 'fixed'], ['confirm-gone', 'src/app.py:2', 'gone'], ['wont-fix', 'src/app.py:3', 'declined'],
    ]) expect((await api.post(`/api/findings/${verb}`, { data: { finding_ids: [ids[locus!]], note } })).status).toBe(200);
    const params = { routine, since: shift(-14).toISOString(), until: shift(1).toISOString(),
      introduced_boundary: new Date(t0.getTime() - 3600000).toISOString(), period_days: 7 };
    const trend = await api.get('/api/routines/trend', { params });
    expect(trend.status).toBe(200);
    const body = trend.body;
    expect(body.routine_name).toBe(routine);
    expect(body.period_days).toBe(7);
    expect(body.periods).toHaveLength(3);
    expect(body.periods.map((period: { created: number }) => period.created)).toEqual([0, 0, 3]);
    expect(body.periods.at(-1).exits).toEqual({ 'gone-confirmed': 1, 'not-a-finding': 0, resolved: 1, superseded: 0, 'wont-fix': 1 });
    expect(body.periods.at(-1)).toMatchObject({ outflow: 2, withdrawn: 1 });
    expect(body.age).toMatchObject({ recent: 2, older: 0, unattributed: 1 });
    const later = await api.get('/api/routines/trend', { params: { ...params, introduced_boundary: shift(1).toISOString() } });
    expect(later.body.age).toMatchObject({ recent: 0, older: 2, unattributed: 1 });
    const args = ['hub', 'routine', 'trend', routine, '--since', local(shift(-14)), '--until', local(shift(1)),
      '--introduced-boundary', local(new Date(t0.getTime() - 3600000))];
    const output = await cli([...args, '--period-days', '7', '--json']);
    expect(output.code).toBe(0);
    const fromCli = JSON.parse(output.stdout);
    expect(fromCli.periods.map((period: { created: number }) => period.created)).toEqual([0, 0, 3]);
    expect(fromCli.periods.at(-1)).toMatchObject({ outflow: 2, withdrawn: 1 });
    expect(fromCli.age).toMatchObject({ recent: 2, unattributed: 1 });
    const rendered = await cli(args);
    expect(rendered.code).toBe(0);
    expect(rendered.stdout).toContain('outflow=2');
    expect(rendered.stdout).toContain('withdrawn=1');
    expect(rendered.stdout).toContain('recent=2');
    expect(rendered.stdout).toContain('unattributed=1');
    expect((await api.get('/api/routines/trend', { params: { ...params, period_days: 0 } })).status).toBe(422);
  });

  test('test_sweeps_reports_last_swept_across_scopes_and_the_windowed_measurement_series', async ({ api, forge, repository, mockRunner, cli }) => {
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    expect(commit.status).toBe(200);
    const g = await garden(api, await mockRunner('garden'), repo, commit.body.sha);
    const t0 = new Date();
    const shifted = (days: number) => new Date(t0.getTime() + days * 86400000);
    const local = (date: Date) => [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-')
      + `T${[date.getHours(), date.getMinutes(), date.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':')}`;
    const notLinked = 'garden-svc-not-linked';
    const retiredSwept = 'garden-svc-retired';
    for (const slug of [notLinked, retiredSwept]) expect((await api.post('/api/scopes', { data: { slug, description: '' } })).status).toBe(201);
    expect((await api.put(`/api/routines/${g.routineId}/scopes/${retiredSwept}`)).status).toBe(204);
    const recorded = await deliver(g, [add_op('src/app.py:1')]);
    expect(recorded.body.outcome).toBe('recorded');
    expect((await deliver(g, [add_op('src/app.py:2')], { scope: retiredSwept })).status).toBe(200);
    expect((await api.post(`/api/scopes/${retiredSwept}/retire`, { data: { by: 'operator' } })).status).toBe(202);
    const response = await api.get(`/api/routines/${g.routineId}/sweeps`, { params: { since: shifted(-1).toISOString(), until: shifted(1).toISOString() } });
    expect(response.status).toBe(200);
    expect(response.body.routine_name).toBe(routine);
    const byScope = Object.fromEntries(response.body.last_swept.map((row: { scope_slug: string }) => [row.scope_slug, row]));
    expect(byScope[notLinked]).toBeUndefined();
    expect(byScope[scope]!.finding_set_id).not.toBeNull();
    expect(byScope[retiredSwept]!.finding_set_id).not.toBeNull();
    expect(new Set(response.body.measurements.map((row: { scope_slug: string }) => row.scope_slug))).toEqual(new Set([scope, retiredSwept]));
    for (const row of response.body.measurements) expect(row.measurement).toBe('service sweep');
    const output = await cli(['hub', 'routine', 'sweeps', routine, '--since', local(shifted(-1)), '--until', local(shifted(1)), '--json']);
    expect(output.code).toBe(0);
    expect(new Set(JSON.parse(output.stdout).last_swept.map((row: { scope_slug: string }) => row.scope_slug))).toEqual(new Set([scope, retiredSwept]));
  });

  test('test_retiring_a_scope_does_not_hide_its_already_delivered_findings', async ({ api, forge, repository, mockRunner }) => {
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    expect(commit.status).toBe(200);
    const g = await garden(api, await mockRunner('garden'), repo, commit.body.sha);
    const recorded = await deliver(g, [add_op('src/app.py:1')]);
    expect(recorded.body.outcome).toBe('recorded');
    const before = await live(g);
    expect(before).toHaveLength(1);
    expect((await api.post(`/api/scopes/${scope}/retire`, { data: { by: 'operator' } })).status).toBe(202);
    const after = await api.get('/api/findings', { params: { scope } });
    expect(after.status).toBe(200);
    expect(after.body.findings.map((row: { finding_id: string }) => row.finding_id)).toEqual(before.map((row) => row.finding_id));
    expect(after.body.findings[0].state).toBe(before[0]!.state);
  });

  test('test_delivering_an_accepted_proposals_minted_item_closes_its_findings_to_delivered', async ({ api, forge, repository, mockRunner }) => {
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    expect(commit.status).toBe(200);
    const g = await garden(api, await mockRunner('garden'), repo, commit.body.sha);
    const [first, second] = await seed(g, 2) as [string, string];
    const proposed = await deliver(g, [{ op: 'observed', id: first }, { op: 'observed', id: second }], { proposals: [
      { ref: 'P1', class: 'handoff', title: 'answer both weeds', body: 'one response covering both findings', findings: [first, second] },
    ] });
    expect(proposed.body.outcome).toBe('recorded');
    const listed = await api.get('/api/garden-proposals');
    expect(listed.body.proposals).toHaveLength(1);
    const proposal = listed.body.proposals[0];
    expect(proposal.findings).toEqual([first, second]);
    const accepted = await api.post(`/api/garden-proposals/${proposal.proposal_id}/accept`, { data: { reason: 'taking it' } });
    expect(accepted.status).toBe(200);
    expect(accepted.body.closure.item_outcome).toBe('minted');
    const pointer = `${accepted.body.closure.source}:${accepted.body.closure.ref}`;
    expect((await read_back(g, first)).state).toBe('live');
    await markMerged(g, accepted.body.chunk_id);
    await api.sweep('close_drain');
    for (const id of [first, second]) expect(await read_back(g, id)).toMatchObject({ state: 'delivered', note: `delivered by ${pointer}` });
  });

  test('test_an_operator_proposal_over_two_routines_and_scopes_delivers_its_findings', async ({ api, forge, repository, mockRunner, cli }, testInfo) => {
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    expect(commit.status).toBe(200);
    const g = await garden(api, await mockRunner('garden'), repo, commit.body.sha);
    const [first] = await seed(g, 1) as [string];
    const secondScope = 'garden-svc-2';
    const secondRoutine = 'garden-service-2';
    expect((await api.post('/api/scopes', { data: { slug: secondScope, description: '' } })).status).toBe(201);
    const created = await api.post('/api/routines', { data: {
      name: secondRoutine, graph_name: 'garden-routine', default_scope_slug: secondScope, default_model: [], default_effort: null,
    } });
    expect(created.status).toBe(201);
    const recorded = await deliver(g, [add_op('src/other.py:1')], { scope: secondScope, routineId: created.body.routine_id });
    expect(recorded.body.outcome).toBe('recorded');
    const secondLive = await api.get('/api/findings', { params: { routine: secondRoutine, scope: secondScope } });
    expect(secondLive.status).toBe(200);
    const second = secondLive.body.findings[0].finding_id;
    const bodyPath = testInfo.outputPath('body.md');
    fs.writeFileSync(bodyPath, 'one response covering both findings');
    const output = await cli(['hub', 'garden-proposal', 'create', '--title', 'answer both weeds', '--class', 'handoff',
      '--body-file', bodyPath, '--finding', first, '--finding', second, '--json']);
    expect(output.code).toBe(0);
    const proposal = JSON.parse(output.stdout);
    expect(proposal).toMatchObject({ origin: 'operator', routine_name: null });
    expect(proposal.findings.sort()).toEqual([first, second].sort());
    const accepted = await api.post(`/api/garden-proposals/${proposal.proposal_id}/accept`, { data: { reason: 'taking it' } });
    expect(accepted.status).toBe(200);
    expect(accepted.body.closure.item_outcome).toBe('minted');
    const pointer = `${accepted.body.closure.source}:${accepted.body.closure.ref}`;
    expect((await read_back(g, first)).state).toBe('live');
    await markMerged(g, accepted.body.chunk_id);
    await api.sweep('close_drain');
    for (const id of [first, second]) expect(await read_back(g, id)).toMatchObject({ state: 'delivered', note: `delivered by ${pointer}` });
  });
});

test.describe('routine and scope membership', () => {
  test.use({ isolation: 'common' });

  test('test_sweeps_404s_on_an_unknown_routine_id', async ({ api }, testInfo) => {
    const t0 = new Date();
    const response = await api.get(`/api/routines/rtn_ghost_${testInfo.workerIndex}/sweeps`, {
      params: { since: new Date(t0.getTime() - 86400000).toISOString(), until: new Date(t0.getTime() + 86400000).toISOString() },
    });
    expect(response.status).toBe(404);
  });

  test('test_retiring_a_linked_scope_leaves_the_routine_scope_membership_intact', async ({ api }, testInfo) => {
    const unique = `${testInfo.workerIndex}-${Date.now()}`;
    const slug = `garden-svc-retired-linked-${unique}`;
    const name = `garden-service-linked-${unique}`;
    expect((await api.post('/api/scopes', { data: { slug, description: '' } })).status).toBe(201);
    const graphs = await api.get('/api/graphs');
    expect(graphs.status).toBe(200);
    expect(graphs.body.some((graph: { name: string }) => graph.name === 'garden-routine')).toBe(true);
    const created = await api.post('/api/routines', { data: {
      name, graph_name: 'garden-routine', default_scope_slug: slug, default_model: [], default_effort: null,
    } });
    expect(created.status).toBe(201);
    const id = created.body.routine_id;
    expect((await api.put(`/api/routines/${id}/scopes/${slug}`)).status).toBe(204);
    expect((await api.post(`/api/scopes/${slug}/retire`, { data: { by: 'operator' } })).status).toBe(202);
    const routineScopes = await api.get(`/api/routines/${id}/scopes`);
    expect(routineScopes.status).toBe(200);
    expect(routineScopes.body).toContain(slug);
    const scopeRoutines = await api.get(`/api/scopes/${slug}/routines`);
    expect(scopeRoutines.status).toBe(200);
    expect(scopeRoutines.body).toContain(id);
  });
});

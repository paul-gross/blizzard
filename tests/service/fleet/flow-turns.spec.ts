import { parse } from 'yaml';
import { expect, stepUntil, test, type ForgeApi, type HubApi, type Repository, type RunnerHost, type Wire } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

const fullName = (repo: Repository) => `${repo.owner}/${repo.name}`;

function graph(repos: Repository[], file: string, interval = 1, recordFindings = false): string {
  const branch = `feature/${file.toLowerCase().replace('.md', '')}`;
  const prompt = [
    'import os, pathlib, subprocess',
    `for name in ${JSON.stringify(repos.map((repo) => repo.name))}:`,
    "    path = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name",
    `    subprocess.run(['git', '-C', str(path), 'switch', '-C', ${JSON.stringify(branch)}], check=True)`,
    `    (path / ${JSON.stringify(file)}).write_text('held on a flow\\n')`,
    "    subprocess.run(['git', '-C', str(path), 'add', '-A'], check=True)",
    "    subprocess.run(['git', '-C', str(path), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: flow turn'], check=True)",
    "    sha = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    `    subprocess.run(['git', '-C', str(path), 'push', '--force', 'origin', ${JSON.stringify(branch)}], check=True)`,
    `    subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', ${JSON.stringify(branch)}, '--commit', sha], check=True)`,
    ...(recordFindings ? ["subprocess.run(['blizzard', 'runner', 'artifact', 'create', '--name', 'review-finding-delta'], input='{\"entries\": [{\"ref\": \"F1\", \"disposition\": \"fixed\"}]}', text=True, check=True)"] : []),
  ].join('\n');
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt, ...(recordFindings ? { produces: ['review-finding-delta'] } : {}), judgement: { prompt: "verdict('pass', 'built')\n", choices: { pass: { description: 'Built.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: interval, poll_timeout: 600, judgement: { choices: {
      landed: { description: 'Landed.', to: recordFindings ? 'record-findings' : 'done' }, conflict: { description: 'Conflict.', to: 'build' },
      failure: { description: 'Failure.', to: 'build' }, 'inherited-failure': { description: 'Base failure.', to: 'build' },
    } } },
    ...(recordFindings ? { 'record-findings': { executor: 'hub', action: 'record-findings', judgement: { choices: {
      recorded: { description: 'Recorded.', to: 'done' }, invalid: { description: 'Invalid.', to: 'done' }, failure: { description: 'Failed.', to: 'done' },
    } } } } : {}),
  } });
}

async function lever(forge: ForgeApi, repo: Repository, armed: boolean) {
  const result = armed ? await forge.call('POST', '/_levers/checks_pending', { repo: fullName(repo) })
    : await forge.call('DELETE', `/_levers/checks_pending?repo=${encodeURIComponent(fullName(repo))}`);
  expect(result.status).toBe(200);
}

async function submissions(api: HubApi, id: string) {
  const result = await api.get<Wire.ChunkFlowSubmissionsView>(`/api/chunks/${id}/flow-submissions`);
  expect(result.status).toBe(200);
  return result.body.items;
}

async function turns(api: HubApi, id: string) {
  const all: Wire.SubmissionTurnView[] = [];
  for (const submission of await submissions(api, id)) {
    let before: string | null = null;
    do {
      const query: string = before === null ? '?limit=1' : `?limit=1&before=${encodeURIComponent(before)}`;
      const page: { status: number; body: Wire.SubmissionTurnsPage } = await api.get(`/api/chunks/${id}/flow-submissions/${submission.submission_id}/turns${query}`);
      expect(page.status).toBe(200);
      all.push(...page.body.items);
      before = page.body.next_before;
    } while (before !== null);
  }
  return all.sort((a, b) => a.turn_id.localeCompare(b.turn_id));
}

async function pending(api: HubApi, host: RunnerHost, id: string) {
  const detail = await stepUntil(host, async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body,
    (row) => row.pending !== null || ['done', 'needs_human', 'stopped'].includes(row.status), 80);
  expect(detail.pending).not.toBeNull();
}

async function landed(api: HubApi, host: RunnerHost, id: string) {
  const detail = await stepUntil(host, async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body,
    (row) => ['done', 'needs_human', 'stopped'].includes(row.status), 80);
  expect([detail.status, detail.landed]).toEqual(['done', true]);
  await expect.poll(async () => {
    const rows = await turns(api, id);
    return rows.length > 0 && rows.every((row) => row.ended_at !== null);
  }).toBe(true);
  return detail;
}

async function landingFacts(api: HubApi, id: string) {
  const facts: Wire.LandingFactView[] = [];
  let cursor: number | null = 0;
  while (cursor !== null) {
    const page: { status: number; body: { items: Wire.LandingFactView[]; next_cursor: number | null } } = await api.get(`/api/chunks/${id}/landing-facts?limit=1&cursor=${cursor}`);
    expect(page.status).toBe(200);
    facts.push(...page.body.items);
    cursor = page.body.next_cursor;
  }
  return facts;
}

test('test_two_repositories_land_side_by_side', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.setTimeout(240_000);
  const apiRepo = await repository();
  const webRepo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [apiRepo.name]: apiRepo.origin!, [webRepo.name]: webRepo.origin! } });
  await lever(forge, apiRepo, true);
  await lever(forge, webRepo, true);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph([apiRepo], 'API.md') } })).status).toBe(201);
  const apiId = (await issue('API', { repository: apiRepo })).chunk.chunk_id;
  expect((await api.post(`/api/chunks/${apiId}/promote`)).status).toBe(202);
  await pending(api, host, apiId);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph([webRepo], 'WEB.md') } })).status).toBe(201);
  const webId = (await issue('WEB', { repository: webRepo })).chunk.chunk_id;
  expect((await api.post(`/api/chunks/${webId}/promote`)).status).toBe(202);
  await pending(api, host, webId);
  const apiFlows = (await submissions(api, apiId))[0]!.flow_ids;
  const webFlows = (await submissions(api, webId))[0]!.flow_ids;
  expect(apiFlows).toHaveLength(1);
  expect(webFlows).toHaveLength(1);
  expect(apiFlows).not.toEqual(webFlows);
  const marks = [await turns(api, apiId), await turns(api, webId)].map((rows) => rows.length);
  await expect.poll(async () => (await turns(api, apiId)).length >= marks[0]! + 2 && (await turns(api, webId)).length >= marks[1]! + 2, { timeout: 60_000 }).toBe(true);
  await lever(forge, webRepo, false);
  await landed(api, host, webId);
  expect((await api.get<Wire.ChunkDetail>(`/api/chunks/${apiId}`)).body.status).toBe('delivering');
  await lever(forge, apiRepo, false);
  await landed(api, host, apiId);
  const webFact = (await landingFacts(api, webId))[0]!;
  const apiFact = (await landingFacts(api, apiId))[0]!;
  expect(webFact.repo).toBe(fullName(webRepo));
  expect(apiFact.repo).toBe(fullName(apiRepo));
  expect(webFact.sequence).toBeLessThan(apiFact.sequence);
});

test('test_two_chunks_each_landing_both_repositories_both_land', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.setTimeout(240_000);
  const repos = [await repository(), await repository()];
  const host = await runnerHosts.create({ runnerRepositories: Object.fromEntries(repos.map((repo) => [repo.name, repo.origin!])) });
  for (const repo of repos) await lever(forge, repo, true);
  const ids: string[] = [];
  for (const file of ['ONE.md', 'TWO.md']) {
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repos, file) } })).status).toBe(201);
    const id = (await issue(file, { repository: repos[0]! })).chunk.chunk_id;
    ids.push(id);
    expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
    await pending(api, host, id);
  }
  const first = (await submissions(api, ids[0]!))[0]!.flow_ids.slice().sort();
  const second = (await submissions(api, ids[1]!))[0]!.flow_ids.slice().sort();
  expect(first).toHaveLength(2);
  expect(second).toEqual(first);
  for (const repo of repos) await lever(forge, repo, false);
  for (const id of ids) {
    await landed(api, host, id);
    const facts = await landingFacts(api, id);
    expect(facts.map((fact) => fact.repo).sort()).toEqual(repos.map(fullName).sort());
  }
});

test('test_a_pending_chunk_yields_its_branch_until_its_next_poll', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.setTimeout(240_000);
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  await lever(forge, repo, true);
  const ids: string[] = [];
  for (const [file, interval] of [['SLOW.md', 6], ['BRISK.md', 1]] as const) {
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph([repo], file, interval) } })).status).toBe(201);
    const id = (await issue(file, { repository: repo })).chunk.chunk_id;
    ids.push(id);
    expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
    await pending(api, host, id);
  }
  const [slow, brisk] = ids as [string, string];
  expect((await submissions(api, slow))[0]!.flow_ids).toEqual((await submissions(api, brisk))[0]!.flow_ids);
  const mark = (await turns(api, slow)).length;
  await expect.poll(async () => (await turns(api, slow)).length, { timeout: 60_000 }).toBeGreaterThanOrEqual(mark + 3);
  const slowTurns = (await turns(api, slow)).slice(mark - 1);
  const briskStarts = (await turns(api, brisk)).map((turn) => Date.parse(turn.started_at));
  for (let i = 0; i + 1 < slowTurns.length; i++) {
    const current = slowTurns[i]!;
    const next = slowTurns[i + 1]!;
    expect(current.outcome).toBe('pending');
    expect(current.ended_at).not.toBeNull();
    const end = Date.parse(current.ended_at!);
    const following = Date.parse(next.started_at);
    expect(following - end).toBeGreaterThanOrEqual(5_000);
    expect(briskStarts.some((start) => end <= start && start < following)).toBe(true);
  }
  await lever(forge, repo, false);
  await landed(api, host, slow);
  await landed(api, host, brisk);
});

test('test_a_chunk_whose_runner_is_gone_lands_and_records_its_findings', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.setTimeout(180_000);
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  await lever(forge, repo, true);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph([repo], 'GONE.md', 1, true) } })).status).toBe(201);
  const id = (await issue('gone', { repository: repo })).chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  await pending(api, host, id);
  await host.stop();
  await lever(forge, repo, false);
  await expect.poll(async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body.status, { timeout: 60_000 }).toBe('done');
  const detail = (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body;
  expect(detail.landed).toBe(true);
  expect(detail.history.slice(-2).map((row) => [row.from_node_name, row.choice_name])).toEqual([['deliver', 'landed'], ['record-findings', 'recorded']]);
  await expect.poll(async () => (await submissions(api, id)).map((row) => row.closure?.outcome), { timeout: 60_000 }).toEqual(['landed', 'recorded']);
  expect((await landingFacts(api, id)).map((row) => row.repo)).toEqual([fullName(repo)]);
  expect((await turns(api, id)).every((row) => row.ended_at !== null)).toBe(true);
});

test.describe('arrival order after restart', () => {
  test.use({ isolation: 'fresh-hub' });
  test('test_two_chunks_on_one_branch_land_in_arrival_order', async ({ repository, issue, api, forge, runnerHosts, freshHub }) => {
    test.setTimeout(240_000);
    const repo = await repository();
    const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
    await lever(forge, repo, true);
    const ids: string[] = [];
    for (const file of ['FIRST.md', 'SECOND.md']) {
      expect((await api.post('/api/graphs', { data: { definition_yaml: graph([repo], file) } })).status).toBe(201);
      const id = (await issue(file, { repository: repo })).chunk.chunk_id;
      ids.push(id);
      expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
      await pending(api, host, id);
    }
    const first = (await submissions(api, ids[0]!))[0]!;
    const second = (await submissions(api, ids[1]!))[0]!;
    expect(first.flow_ids).toHaveLength(1);
    expect(first.flow_ids).toEqual(second.flow_ids);
    expect(first.arrival).toBeLessThan(second.arrival);
    await freshHub.stop();
    await lever(forge, repo, false);
    await freshHub.start();
    await landed(api, host, ids[0]!);
    await landed(api, host, ids[1]!);
    const firstFact = (await landingFacts(api, ids[0]!))[0]!;
    const secondFact = (await landingFacts(api, ids[1]!))[0]!;
    expect(firstFact.repo).toBe(fullName(repo));
    expect(secondFact.repo).toBe(fullName(repo));
    expect(firstFact.sequence).toBeLessThan(secondFact.sequence);
  });
});

test('test_a_chunk_paused_at_a_hub_node_takes_no_turn_until_resumed', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.setTimeout(240_000);
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  await lever(forge, repo, true);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph([repo], 'BARRIER.md') } })).status).toBe(201);
  const barrier = (await issue('barrier', { repository: repo })).chunk.chunk_id;
  expect((await api.post(`/api/chunks/${barrier}/promote`)).status).toBe(202);
  await pending(api, host, barrier);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph([repo], 'PAUSED.md') } })).status).toBe(201);
  const id = (await issue('paused', { repository: repo })).chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  const runner = host.runners['runner-local']!;
  const facts = await stepUntil(host, async () => (await runner.facts({ limit: 200 })).body.items,
    (rows) => rows.some((row) => row.kind === 'completion.submitted' && row.chunk_id === id && row.acked_at === null), 80);
  const completion = facts.find((row) => row.kind === 'completion.submitted' && row.chunk_id === id && row.acked_at === null)!;
  expect(completion.lease_id).not.toBeNull();
  expect((await api.post(`/api/chunks/${id}/pause`, { data: { by: 'e2e' } })).status).toBe(202);
  const paused = await stepUntil(host, async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body,
    (row) => row.history.some((entry) => entry.to_node_name === 'deliver'), 80);
  expect(paused.status).toBe('paused');
  const acknowledged = (await runner.facts({ limit: 200 })).body.items.find((row) => row.seq === completion.seq);
  expect(acknowledged).toBeDefined();
  expect(acknowledged!.acked_at).not.toBeNull();
  const mark = (await turns(api, barrier)).length;
  await expect.poll(async () => (await turns(api, barrier)).length, { timeout: 60_000 }).toBeGreaterThanOrEqual(mark + 2);
  expect(await turns(api, id)).toEqual([]);
  expect((await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body.status).toBe('paused');
  expect((await api.post(`/api/chunks/${id}/resume`, { data: { by: 'e2e' } })).status).toBe(202);
  await lever(forge, repo, false);
  await landed(api, host, barrier);
  await landed(api, host, id);
  expect((await turns(api, id)).length).toBeGreaterThan(0);
});

test('test_a_garden_delivery_lands_while_a_chunk_is_held_on_a_branch', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.setTimeout(240_000);
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  await lever(forge, repo, true);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph([repo], 'HELD.md') } })).status).toBe(201);
  const held = (await issue('held', { repository: repo })).chunk.chunk_id;
  expect((await api.post(`/api/chunks/${held}/promote`)).status).toBe(202);
  await pending(api, host, held);
  const listed = await api.get<{ graph_id: string; name: string }[]>('/api/graphs');
  const packaged = listed.body.find((row) => row.name === 'garden-routine');
  expect(packaged).toBeDefined();
  const authoring = await api.get<{ definition_yaml: string }>(`/api/graphs/${packaged!.graph_id}`);
  const gardenGraph = parse(authoring.body.definition_yaml) as { nodes: Record<string, { prompt: string; judgement: { prompt: string } }> };
  gardenGraph.nodes.survey!.prompt = [
    'import json, subprocess',
    `rev = subprocess.run(['git', '-C', ${JSON.stringify(repo.name)}, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()`,
    `revision = {${JSON.stringify(repo.name)}: rev}`,
    "survey = {'scope': 'flow-garden-clean', 'revisions': revision, 'measurement': 'clean', 'candidates': []}",
    "delta = {'scope': 'flow-garden-clean', 'revisions': revision, 'measurement': 'clean', 'findings': []}",
    "for name, data in [('survey', survey), ('delta', delta)]:",
    "    subprocess.run(['blizzard', 'runner', 'artifact', 'create', '--name', name], input=json.dumps(data), text=True, check=True)",
  ].join('\n');
  gardenGraph.nodes.survey!.judgement.prompt = "verdict('clean', 'clean sweep')\n";
  expect((await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(gardenGraph) } })).status).toBe(201);
  const routine = await api.post<{ routine_id: string }>('/api/routines', { data: {
    name: 'flow-garden', graph_name: 'garden-routine', default_scope_slug: 'flow-garden-clean', default_model: [], default_effort: null,
  } });
  expect(routine.status).toBe(201);
  const run = await api.post<{ chunk_id: string }>(`/api/routines/${routine.body.routine_id}/run`, { data: { scope_slug: 'flow-garden-clean', mode: 'full', note: 'path=clean' } });
  expect(run.status).toBe(201);
  const garden = run.body.chunk_id;
  expect((await api.post(`/api/chunks/${garden}/promote`)).status).toBe(202);
  const detail = await stepUntil(host, async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${garden}`)).body,
    (row) => ['done', 'needs_human', 'stopped'].includes(row.status), 100);
  expect(detail.status).toBe('done');
  expect(detail.history.map((row) => [row.from_node_name, row.choice_name])).toEqual([['survey', 'clean'], ['deliver', 'recorded']]);
  const gardenSubmissions = await submissions(api, garden);
  expect(gardenSubmissions).toHaveLength(1);
  expect(gardenSubmissions[0]!.flow_ids).toEqual([]);
  expect(gardenSubmissions[0]!.closure?.outcome).toBe('recorded');
  expect((await turns(api, garden)).length).toBeGreaterThan(0);
  expect((await turns(api, garden)).every((row) => row.ended_at !== null)).toBe(true);
  expect((await submissions(api, held))[0]!.closure).toBeNull();
  expect((await api.get<Wire.ChunkDetail>(`/api/chunks/${held}`)).body.status).toBe('delivering');
  await lever(forge, repo, false);
  await landed(api, host, held);
});

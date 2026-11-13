import { chunkProgress, expect, stepUntil, test, type ForgeApi, type HubApi, type Repository, type Wire } from '../../harness/fixtures';

test.use({ isolation: 'fresh-hub', clockMode: 'controlled', winterForge: true });

function graph(repo: Repository, checkpoint: string): string {
  const prompt = [
    'import os, pathlib, subprocess',
    `path = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / ${JSON.stringify(repo.name)}`,
    "subprocess.run(['git', '-C', str(path), 'switch', '-C', 'feature/acceptance'], check=True)",
    "(path / 'ACCEPTANCE.md').write_text('winter forge acceptance\\n')",
    "subprocess.run(['git', '-C', str(path), 'add', '-A'], check=True)",
    "subprocess.run(['git', '-C', str(path), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: acceptance'], check=True)",
    "sha = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    "subprocess.run(['git', '-C', str(path), 'push', 'origin', 'feature/acceptance'], check=True)",
    `subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', ${JSON.stringify(repo.name)}, '--branch', 'feature/acceptance', '--commit', sha], check=True)`,
    checkpoint,
  ].join('\n');
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt, judgement: { prompt: "verdict('pass', 'built')\n", choices: { pass: { description: 'Built.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: 3600, judgement: { choices: {
      landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' }, failure: { description: 'Failure.', to: 'build' }, 'inherited-failure': { description: 'Base failure.', to: 'build' },
    } } },
  } });
}

test('status evidence: denied, pending, superseded success', async ({ repository, issue, api, cli, clock, forge, runnerHosts, workerCheckpoint }) => {
  test.setTimeout(180_000);
  const repo = await repository();
  console.log(`winter origin: ${full(repo)} ${repo.origin}`);
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const before = await host.originHead(repo.name, 'main');
  expect((await api.patch(`/api/repositories/${repo.name}`, { data: { allow_no_ci: true } })).status).toBe(200);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repo, workerCheckpoint.snippet('pushed')) } })).status).toBe(201);
  const { chunk } = await issue('status evidence', { repository: repo });
  const id = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  let arrived = false;
  void workerCheckpoint.reached('pushed').then(() => { arrived = true; });
  await stepUntil(host, async () => arrived, Boolean, { stallPasses: 80 });
  const sha = await host.originHead(repo.name, 'feature/acceptance');
  expect(sha).toBeTruthy();
  await lever(forge, 'ref_checks', { repo: full(repo), ref: sha, checks: [] });
  await lever(forge, 'status_read_error', { repo: full(repo), ref: sha, status_code: 403 });
  expect((await forge.call('GET', `/repos/${full(repo)}/commits/${sha}/statuses`)).status).toBe(403);
  workerCheckpoint.release('pushed');
  const denied = await stepUntil(host, () => detail(api, id), (row) => row.status === 'needs_human', { stallPasses: 80, progress: chunkProgress });
  expect(denied.landed).toBe(false);
  expect(denied.escalation?.cause).toBe('forge-auth');
  expect(await host.originHead(repo.name, 'main')).toBe(before);
  expect(await facts(api, id)).toEqual([]);
  await lever(forge, 'status_read_error', { repo: full(repo), ref: sha, status_code: null });
  await lever(forge, 'ref_statuses', { repo: full(repo), ref: sha, statuses: [{ context: 'ci', state: 'pending' }] });
  const pendingStatus = await forge.call('GET', `/repos/${full(repo)}/commits/${sha}/statuses`);
  expect(pendingStatus.status).toBe(200);
  expect(pendingStatus.body).toEqual([expect.objectContaining({ context: 'ci', state: 'pending' })]);
  await clock.advance(1);
  const requeue = await cli(['hub', 'chunk', 'requeue', id]);
  expect(requeue.code, requeue.stderr).toBe(0);
  await clock.advance(1);
  const pending = await stepUntil(host, () => detail(api, id), (row) => row.flow_record.some((entry) => entry.outcome === 'pending'), { stallPasses: 80, progress: chunkProgress });
  expect(pending.landed).toBe(false);
  expect(await host.originHead(repo.name, 'main')).toBe(before);
  expect(await facts(api, id)).toEqual([]);
  await lever(forge, 'ref_statuses', { repo: full(repo), ref: sha, statuses: [{ context: 'ci', state: 'success' }, { context: 'ci', state: 'failure' }] });
  const superseded = await forge.call('GET', `/repos/${full(repo)}/commits/${sha}/statuses`);
  expect(superseded.body).toEqual([expect.objectContaining({ context: 'ci', state: 'success' }), expect.objectContaining({ context: 'ci', state: 'failure' })]);
  await clock.advance(2);
  const done = await stepUntil(host, () => detail(api, id), (row) => row.status === 'done', { stallPasses: 80, progress: chunkProgress });
  const after = await host.originHead(repo.name, 'main');
  expect(done.landed).toBe(true);
  expect(after).not.toBe(before);
  expect(await facts(api, id)).toEqual([expect.objectContaining({ repo: full(repo), commit_hash: after })]);
  console.log(JSON.stringify({ id, before, sha, after, facts: await facts(api, id) }));
});

test('external merge after escalation reconciles exactly once', async ({ repository, issue, api, cli, clock, forge, runnerHosts, workerCheckpoint }) => {
  test.setTimeout(180_000);
  const repo = await repository();
  console.log(`winter origin: ${full(repo)} ${repo.origin}`);
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const before = await host.originHead(repo.name, 'main');
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repo, workerCheckpoint.snippet('pushed')) } })).status).toBe(201);
  const { chunk } = await issue('external merge', { repository: repo });
  const id = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  let arrived = false;
  void workerCheckpoint.reached('pushed').then(() => { arrived = true; });
  await stepUntil(host, async () => arrived, Boolean, { stallPasses: 80 });
  const sha = await host.originHead(repo.name, 'feature/acceptance');
  await lever(forge, 'ref_checks', { repo: full(repo), ref: sha, checks: [] });
  workerCheckpoint.release('pushed');
  const pending = await stepUntil(host, () => detail(api, id), (row) => row.pending !== null, { stallPasses: 80, progress: chunkProgress });
  expect(pending.open_prs).toHaveLength(1);
  await clock.advance(601);
  const escalated = await stepUntil(host, () => detail(api, id), (row) => row.status === 'needs_human', { stallPasses: 80, progress: chunkProgress });
  expect(escalated.escalation?.cause).toBe('missing-checks');
  expect(await host.originHead(repo.name, 'main')).toBe(before);
  expect(await facts(api, id)).toEqual([]);
  const merged = await forge.call('PUT', `/repos/${full(repo)}/pulls/${pending.open_prs[0]!.number}/merge`, { merge_method: 'merge' });
  expect(merged.status, JSON.stringify(merged.body)).toBe(200);
  const after = await host.originHead(repo.name, 'main');
  expect(after).not.toBe(before);
  await clock.advance(1);
  const requeue = await cli(['hub', 'chunk', 'requeue', id]);
  expect(requeue.code, requeue.stderr).toBe(0);
  await clock.advance(1);
  const done = await stepUntil(host, () => detail(api, id), (row) => row.status === 'done', { stallPasses: 80, progress: chunkProgress });
  expect(done.landed).toBe(true);
  expect(await facts(api, id)).toEqual([expect.objectContaining({ repo: full(repo), commit_hash: after })]);
  expect(await host.originHead(repo.name, 'main')).toBe(after);
  console.log(JSON.stringify({ id, before, sha, after, facts: await facts(api, id) }));
});

const full = (repo: Repository) => `${repo.owner}/${repo.name}`;
async function lever(forge: ForgeApi, name: string, data: unknown) {
  const result = await forge.call('POST', `/_levers/${name}`, data);
  expect(result.status, JSON.stringify(result.body)).toBe(200);
}
async function detail(api: HubApi, id: string): Promise<Wire.ChunkDetail> {
  await api.flowStep();
  const result = await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`);
  expect(result.status).toBe(200);
  return result.body;
}
async function terms(api: HubApi, id: string): Promise<boolean[]> {
  const result = await api.get<{ items: { terms: { allow_no_ci: boolean }[] }[] }>(`/api/chunks/${id}/flow-submissions`);
  expect(result.status).toBe(200);
  return result.body.items.flatMap((row) => row.terms.map((term) => term.allow_no_ci));
}
async function facts(api: HubApi, id: string) {
  const result = await api.get<{ items: { repo: string; commit_hash: string }[] }>(`/api/chunks/${id}/landing-facts`);
  expect(result.status).toBe(200);
  return result.body.items;
}

for (const policy of ['fast-forward', 'merge-on-checks'] as const) {
  test(`strict grace, snapshot and opt-in requeue: ${policy}`, async ({ repository, issue, api, cli, clock, forge, runnerHosts, workerCheckpoint }) => {
    test.setTimeout(180_000);
    const repo = await repository();
    console.log(`winter origin: ${full(repo)} ${repo.origin}`);
    const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
    const before = await host.originHead(repo.name, 'main');
    expect((await api.patch(`/api/repositories/${repo.name}`, { data: { landing_policy: policy } })).status).toBe(200);
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repo, workerCheckpoint.snippet('pushed')) } })).status).toBe(201);
    const { chunk } = await issue('strict empty acceptance', { repository: repo });
    const id = chunk.chunk_id;
    expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
    let arrived = false;
    void workerCheckpoint.reached('pushed').then(() => { arrived = true; });
    await stepUntil(host, async () => arrived, Boolean, { stallPasses: 80 });
    const sha = await host.originHead(repo.name, 'feature/acceptance');
    expect(sha).toBeTruthy();
    await lever(forge, 'ref_checks', { repo: full(repo), ref: sha, checks: [] });
    await lever(forge, 'ref_statuses', { repo: full(repo), ref: sha, statuses: [] });
    const checks = await forge.call('GET', `/repos/${full(repo)}/commits/${sha}/check-runs`);
    const statuses = await forge.call('GET', `/repos/${full(repo)}/commits/${sha}/statuses`);
    expect(checks.status).toBe(200);
    expect(checks.body.check_runs).toEqual([]);
    expect(statuses.status).toBe(200);
    expect(statuses.body).toEqual([]);
    workerCheckpoint.release('pushed');
    const pending = await stepUntil(host, () => detail(api, id), (row) => row.pending !== null, { stallPasses: 80, progress: chunkProgress });
    expect(pending.landed).toBe(false);
    expect(await terms(api, id)).toEqual([false]);
    expect(await facts(api, id)).toEqual([]);
    expect(await host.originHead(repo.name, 'main')).toBe(before);
    const edit = await cli(['hub', 'repo', 'edit', repo.name, '--allow-no-ci']);
    expect(edit.code, edit.stderr).toBe(0);
    expect(await terms(api, id)).toEqual([false]);
    await clock.advance(599);
    await detail(api, id);
    expect((await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body.status).not.toBe('needs_human');
    await clock.advance(2);
    const escalated = await stepUntil(host, () => detail(api, id), (row) => row.status === 'needs_human', { stallPasses: 80, progress: chunkProgress });
    expect(escalated.escalation?.cause).toBe('missing-checks');
    expect(escalated.escalation?.detail).toContain(sha!);
    expect(await facts(api, id)).toEqual([]);
    expect(await host.originHead(repo.name, 'main')).toBe(before);
    await clock.advance(1);
    const requeue = await cli(['hub', 'chunk', 'requeue', id]);
    expect(requeue.code, requeue.stderr).toBe(0);
    await clock.advance(1);
    const done = await stepUntil(host, () => detail(api, id), (row) => row.status === 'done', { stallPasses: 80, progress: chunkProgress });
    const after = await host.originHead(repo.name, 'main');
    expect(done.landed).toBe(true);
    expect(after).not.toBe(before);
    expect(await terms(api, id)).toEqual([false, true]);
    expect(await facts(api, id)).toEqual([expect.objectContaining({ repo: full(repo), commit_hash: after })]);
    console.log(JSON.stringify({ id, policy, before, sha, after, terms: await terms(api, id), facts: await facts(api, id) }));
  });
}

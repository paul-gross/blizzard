import { expect, stepUntil, test, type ForgeApi, type HubApi, type Repository, type Wire } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

const fullName = (repo: Repository) => `${repo.owner}/${repo.name}`;

function graph(repos: Repository[], extra = '', timeout = 600): string {
  const prompt = [
    'import os, pathlib, subprocess',
    `for name in ${JSON.stringify(repos.map((repo) => repo.name))}:`,
    "    path = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name",
    "    subprocess.run(['git', '-C', str(path), 'switch', '-C', 'feature/diverged'], check=True)",
    "    (path / 'FAST-FORWARD.md').write_text('change\\n')",
    "    subprocess.run(['git', '-C', str(path), 'add', '-A'], check=True)",
    "    subprocess.run(['git', '-C', str(path), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: change'], check=True)",
    "    commit = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    "    subprocess.run(['git', '-C', str(path), 'push', 'origin', 'feature/diverged'], check=True)",
    "    subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', 'feature/diverged', '--commit', commit], check=True)",
    extra,
  ].join('\n');
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt, judgement: { prompt: "verdict('pass', 'built')\n", choices: { pass: { description: 'Built.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: timeout, judgement: { choices: {
      landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
      failure: { description: 'Failure.', to: 'build' }, 'inherited-failure': { description: 'Base failure.', to: 'build' },
    } } },
  } });
}

test('test_fast_forward_stands_when_second_forge_refuses_merge_method', async ({ repository, issue, api, forge, secondForge, runnerHosts, project }) => {
  test.setTimeout(180_000);
  const first = await repository();
  const name = `${first.name}-second`;
  const made = await secondForge.createRepository(first.owner, name);
  const second: Repository = { name, owner: first.owner, tokenPrefix: name, origin: made.origin };
  const applied = await api.post('/api/config/apply', { data: {
    version: 1, secrets: ['fixture-forge-token'],
    work_sources: [{ slug: name, provider: 'github', locator: fullName(second), api_base: secondForge.url, secret: 'fixture-forge-token' }],
    repositories: [{ slug: name, forge_api_url: secondForge.url, owner: second.owner, repo: name, base_branch: 'main', secret_name: 'fixture-forge-token' }],
    projects: [{ slug: project.slug, repositories: [first.name, name], sources: [first.name, name] }],
  } });
  expect(applied.status, JSON.stringify(applied.body)).toBe(200);
  expect((await api.patch(`/api/repositories/${first.name}`, { data: { landing_policy: 'fast-forward' } })).status).toBe(200);
  expect((await secondForge.call('POST', '/_levers/merge_commits_disallowed', { repo: fullName(second) })).status).toBe(200);
  const host = await runnerHosts.create({ runnerRepositories: { [first.name]: first.origin!, [name]: second.origin! } });
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph([first, second]) } })).status).toBe(201);
  const { chunk } = await issue('CROSS-FORGE.md', { repository: first });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const done = await stepUntil(host, () => detail(api, chunk.chunk_id), (row) => row.status === 'needs_human', 80);
  expect(done.escalation?.cause).toBe('merge-method-refused');
  expect(done.landed_repos.map((row) => row.repo)).toEqual([fullName(first)]);
  expect(await pulls(forge, first)).toEqual([]);
  expect(await pulls(secondForge, second, 'open')).toHaveLength(1);
});

test('test_candidate_without_checks_exhausts_pending_timeout_without_advancing', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.setTimeout(180_000);
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  expect((await api.patch(`/api/repositories/${repo.name}`, { data: { landing_policy: 'fast-forward' } })).status).toBe(200);
  const extra = [
    'import json, urllib.request',
    `request = urllib.request.Request(${JSON.stringify(`${forge.url}/_levers/ref_checks`)}, data=json.dumps({'repo': ${JSON.stringify(fullName(repo))}, 'ref': commit, 'checks': []}).encode(), headers={'Content-Type': 'application/json'}, method='POST')`,
    'urllib.request.urlopen(request).close()',
  ].join('\n');
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph([repo], extra, 2) } })).status).toBe(201);
  const { chunk } = await issue('NO-CHECKS.md', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const original = await forge.call('GET', `/repos/${fullName(repo)}/git/ref/heads/main`);
  await stepUntil(host, () => detail(api, chunk.chunk_id), (row) => row.pending?.kind === 'pending', 80);
  await expect.poll(async () => (await detail(api, chunk.chunk_id)).history.some((row) => row.from_node_name === 'deliver' && row.choice_name === 'failure'), { timeout: 30_000 }).toBe(true);
  const result = await detail(api, chunk.chunk_id);
  expect(result.landed_repos).toEqual([]);
  const base = await forge.call('GET', `/repos/${fullName(repo)}/git/ref/heads/main`);
  expect(base.body.object.sha).toBe(original.body.object.sha);
  expect(await pulls(forge, repo)).toEqual([]);
});

async function detail(api: HubApi, id: string): Promise<Wire.ChunkDetail> {
  const response = await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`);
  expect(response.status).toBe(200);
  return response.body;
}

async function pulls(forge: ForgeApi, repo: Repository, state = 'all') {
  const response = await forge.call('GET', `/repos/${fullName(repo)}/pulls?state=${state}`);
  expect(response.status).toBe(200);
  return response.body as unknown[];
}

for (const protectedBase of [false, true]) {
  test(`test_ahead_fast_forward_and_protected_base[${protectedBase ? 'True' : 'False'}]`, async ({ repository, issue, api, forge, runnerHosts }) => {
    test.setTimeout(180_000);
    const repo = await repository();
    const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
    expect((await api.patch(`/api/repositories/${repo.name}`, { data: { landing_policy: 'fast-forward' } })).status).toBe(200);
    if (protectedBase) expect((await forge.call('POST', '/_levers/branch_protected', { repo: fullName(repo), branch: 'main' })).status).toBe(200);
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph([repo]) } })).status).toBe(201);
    const { chunk } = await issue('FAST-FORWARD.md', { repository: repo });
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const done = await stepUntil(host, () => detail(api, chunk.chunk_id), (row) => ['done', 'needs_human', 'stopped'].includes(row.status), 80);
    expect(done.status).toBe(protectedBase ? 'needs_human' : 'done');
    expect(await pulls(forge, repo)).toEqual([]);
  });
}

for (const scenario of ['diverged', 'stale-candidate', 'protected-landing'] as const) {
  test(`test_diverged_base_creates_and_cleans_proven_candidate[${scenario}]`, async ({ repository, issue, api, forge, runnerHosts }) => {
    test.setTimeout(180_000);
    const repo = await repository();
    const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
    expect((await api.patch(`/api/repositories/${repo.name}`, { data: { landing_policy: 'fast-forward' } })).status).toBe(200);
    if (scenario === 'protected-landing') expect((await forge.call('POST', '/_levers/ruleset', { repo: fullName(repo), pattern: 'feature/diverged-landing' })).status).toBe(200);
    const extra = [
      "subprocess.run(['git', '-C', str(path), 'fetch', 'origin', 'main'], check=True)",
      "subprocess.run(['git', '-C', str(path), 'switch', '-C', 'base-update', 'origin/main'], check=True)",
      "(path / 'BASE-UPDATE.md').write_text('base moved\\n')",
      "subprocess.run(['git', '-C', str(path), 'add', '-A'], check=True)",
      "subprocess.run(['git', '-C', str(path), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'base update'], check=True)",
      "subprocess.run(['git', '-C', str(path), 'push', 'origin', 'HEAD:main'], check=True)",
      ...(scenario === 'stale-candidate' ? ["subprocess.run(['git', '-C', str(path), 'push', 'origin', commit + ':refs/heads/feature/diverged-landing'], check=True)"] : []),
    ].join('\n');
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph([repo], extra) } })).status).toBe(201);
    const { chunk } = await issue('DIVERGED.md', { repository: repo });
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const done = await stepUntil(host, () => detail(api, chunk.chunk_id), (row) => ['done', 'needs_human', 'stopped'].includes(row.status), 80);
    if (scenario === 'protected-landing') {
      expect(done.status).toBe('needs_human');
      expect(done.escalation?.cause).toBe('branch-protected');
      expect(done.escalation?.detail).toContain('feature/diverged-landing');
      expect(done.landed_repos).toEqual([]);
      return;
    }
    expect([done.status, done.landed]).toEqual(['done', true]);
    const base = await forge.call('GET', `/repos/${fullName(repo)}/git/ref/heads/main`);
    const feature = await forge.call('GET', `/repos/${fullName(repo)}/git/ref/heads/feature/diverged`);
    const commit = await forge.call('GET', `/repos/${fullName(repo)}/commits/${base.body.object.sha}`);
    expect(commit.body.parents).toHaveLength(2);
    expect(commit.body.parents[1].sha).toBe(feature.body.object.sha);
    const candidate = await forge.call('GET', `/repos/${fullName(repo)}/git/ref/heads/feature/diverged-landing`);
    expect(candidate.status).toBe(422);
    expect(candidate.body.message).toBe(`ref does not resolve in ${fullName(repo)}: feature/diverged-landing`);
  });
}

import { chunkProgress, expect, stepUntil, test, type ForgeApi, type HubApi, type Repository, type RunnerHost, type Wire } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

const REWRITE = 'rust-rewrite';
const fullName = (repo: Repository) => `${repo.owner}/${repo.name}`;

// Each chunk commits a file named for itself on its own feature branch, cut from the worktree's HEAD as prepared.
function graph(repo: Repository): string {
  const prompt = [
    'import os, pathlib, subprocess',
    "chunk = os.environ['BLIZZARD_CHUNK_ID']",
    `name = '${repo.name}'`,
    "path = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name)",
    "branch = f'feature/{chunk}'",
    "subprocess.run(['git', '-C', path, 'switch', '-C', branch], check=True)",
    "(pathlib.Path(path) / f'{chunk}.md').write_text('landed by the mock harness\\n')",
    "subprocess.run(['git', '-C', path, 'add', '-A'], check=True)",
    "subprocess.run(['git', '-C', path, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', f'feat: {chunk}'], check=True)",
    "sha = subprocess.run(['git', '-C', path, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    "subprocess.run(['git', '-C', path, 'push', 'origin', branch], check=True)",
    "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', branch, '--commit', sha], check=True)",
    '',
  ].join('\n');
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt, judgement: { prompt: "verdict('pass', 'built')\n", choices: { pass: { description: 'Built.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: {
      landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
      failure: { description: 'Failure.', to: 'build' }, 'inherited-failure': { description: 'Base failure.', to: 'build' },
    } } },
  } });
}

test('test_two_projects_on_one_repository_land_on_their_own_branches_through_one_reused_environment', async ({ repository, api, forge, project, runnerHosts }) => {
  test.setTimeout(240_000);
  const repo = await repository();
  const main = await branchSha(forge, repo, 'main');
  expect((await forge.call('POST', `/repos/${fullName(repo)}/git/refs`, { ref: `refs/heads/${REWRITE}`, sha: main })).status).toBe(201);
  const overriding = await secondProject(api, repo);
  // One runner serving both projects from one environment: the second chunk prepares only once the first released it.
  // The worktree's origin names the forge's host and the repository's owner, as a forge origin does, so the
  // repository's target names that worktree and its commits resolve to the repository's record.
  const host = await runnerHosts.create({
    runnerRepositories: { [repo.name]: repo.origin! },
    runnerConfig: {
      workspace: [{ id: 'workspace-local', max_environments: 1n, repo: [{ name: repo.name, url: `file://${new URL(forge.url).host}${repo.origin!}` }] }],
      runner: [{ name: 'runner-local', projects: [{ project: project.slug, workspace: 'workspace-local' }, { project: overriding.slug, workspace: 'workspace-local' }] }],
    },
  });
  await host.originCommit(repo.name, REWRITE, 'REWRITE.md', 'the rewrite\n', 'feat: start the rewrite');
  await setBase(api, overriding.id, repo, REWRITE);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repo) } })).status).toBe(201);

  const rewriteBase = await host.originHead(repo.name, REWRITE);
  const mainBase = await host.originHead(repo.name, 'main');
  const onRewrite = await ingest(api, forge, repo, overriding.slug, 'on the rewrite');
  const onMain = await ingest(api, forge, repo, project.slug, 'on main');
  for (const id of [onRewrite, onMain]) expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  await untilDone(host, api, [onRewrite, onMain]);

  expect(await targetOf(api, onRewrite)).toEqual({ base_branch: REWRITE, inherited: false, captured: true });
  expect(await targetOf(api, onMain)).toEqual({ base_branch: 'main', inherited: true, captured: true });
  expect(await baseOf(host, repo, onRewrite)).toBe(rewriteBase);
  expect(await baseOf(host, repo, onMain)).toBe(mainBase);
  const rewriteTree = await host.originTree(repo.name, REWRITE);
  const mainTree = await host.originTree(repo.name, 'main');
  expect(rewriteTree).toContain(`${onRewrite}.md`);
  expect(rewriteTree).not.toContain(`${onMain}.md`);
  expect(mainTree).toContain(`${onMain}.md`);
  expect(mainTree).not.toContain(`${onRewrite}.md`);
  expect(mainTree).not.toContain('REWRITE.md');

  // Clearing the override sends the project's next chunk to the repository's own branch.
  await setBase(api, overriding.id, repo, null);
  const clearedBase = await host.originHead(repo.name, 'main');
  const rewriteHead = await host.originHead(repo.name, REWRITE);
  const cleared = await ingest(api, forge, repo, overriding.slug, 'after the override is cleared');
  expect((await api.post(`/api/chunks/${cleared}/promote`)).status).toBe(202);
  await untilDone(host, api, [cleared]);
  expect(await targetOf(api, cleared)).toEqual({ base_branch: 'main', inherited: true, captured: true });
  expect(await baseOf(host, repo, cleared)).toBe(clearedBase);
  expect(await host.originTree(repo.name, 'main')).toContain(`${cleared}.md`);
  expect(await host.originHead(repo.name, REWRITE)).toBe(rewriteHead);

  // An override naming a branch the origin lacks never claims and moves no branch.
  await setBase(api, overriding.id, repo, 'no-such-branch');
  const heads = { main: await host.originHead(repo.name, 'main'), [REWRITE]: await host.originHead(repo.name, REWRITE) };
  const missing = await ingest(api, forge, repo, overriding.slug, 'on a branch the origin lacks');
  expect((await api.post(`/api/chunks/${missing}/promote`)).status).toBe(202);
  const refused = await stepUntil(host, () => preparationFailures(api, missing), (rows) => rows.length > 0, { stallPasses: 40 });
  expect(refused[0]!.detail).toMatchObject({ command: 'environment preparation step: target-branch' });
  expect(JSON.stringify(refused[0]!.detail)).toContain('no-such-branch');
  const unclaimed = await detail(api, missing);
  expect(unclaimed.status).toBe('ready');
  expect(unclaimed.repository_targets.map((row) => [row.base_branch, row.captured_at])).toEqual([['no-such-branch', null]]);
  expect({ main: await host.originHead(repo.name, 'main'), [REWRITE]: await host.originHead(repo.name, REWRITE) }).toEqual(heads);
  expect(await host.originHead(repo.name, `feature/${missing}`)).toBeUndefined();
});

async function secondProject(api: HubApi, repo: Repository): Promise<{ id: string; slug: string }> {
  const slug = `${repo.name}-rewrite`;
  const created = await api.post('/api/projects', { data: { slug, name: slug } });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  const id = created.body.project_id as string;
  for (const kind of ['source-links', 'repository-links']) {
    const linked = await api.put(`/api/projects/${id}/${kind}/${repo.name}`);
    expect(linked.status, JSON.stringify(linked.body)).toBe(200);
  }
  return { id, slug };
}

async function setBase(api: HubApi, projectId: string, repo: Repository, branch: string | null): Promise<void> {
  const patched = await api.patch<Wire.RepositoryLinkView>(`/api/projects/${projectId}/repository-links/${repo.name}`, { data: { base_branch: branch } });
  expect(patched.status, JSON.stringify(patched.body)).toBe(200);
  expect(patched.body.effective_base_branch).toBe(branch ?? 'main');
}

async function ingest(api: HubApi, forge: ForgeApi, repo: Repository, project: string, title: string): Promise<string> {
  const issue = await forge.createIssue(fullName(repo), { title });
  const ingested = await api.post<Wire.ChunkIngestResponse>('/api/chunks', { data: { tokens: [`${repo.tokenPrefix}:${issue.number}`], project } });
  expect(ingested.status, JSON.stringify(ingested.body)).toBe(201);
  return ingested.body.chunk_id;
}

async function untilDone(host: RunnerHost, api: HubApi, ids: string[]): Promise<void> {
  const read = () => Promise.all(ids.map((id) => detail(api, id)));
  const settled = await stepUntil(host, read, (rows) => rows.every((row) => ['done', 'needs_human', 'stopped'].includes(row.status)), { stallPasses: 120, progress: (rows) => rows.map(chunkProgress) });
  expect(settled.map((row) => [row.status, row.landed])).toEqual(ids.map(() => ['done', true]));
}

async function detail(api: HubApi, id: string): Promise<Wire.ChunkDetail> {
  const response = await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`);
  expect(response.status).toBe(200);
  return response.body;
}

async function targetOf(api: HubApi, id: string) {
  const [target, ...rest] = (await detail(api, id)).repository_targets;
  expect(rest).toEqual([]);
  return { base_branch: target!.base_branch, inherited: target!.inherited, captured: target!.captured_at !== null };
}

/** The commit a chunk's worker cut its feature branch from: the parent of the one commit it made there. */
async function baseOf(host: RunnerHost, repo: Repository, id: string): Promise<string | undefined> {
  const made = ((await host.originLog(repo.name, `feature/${id}`)) ?? []).filter((commit) => commit.subject === `feat: ${id}`);
  expect(made).toHaveLength(1);
  return made[0]!.parents[0];
}


async function preparationFailures(api: HubApi, id: string) {
  const response = await api.get<{ events: Wire.EventView[] }>('/api/events');
  expect(response.status).toBe(200);
  return response.body.events.filter((row) => row.kind === 'command-failed' && row.chunk_id === id);
}

async function branchSha(forge: ForgeApi, repo: Repository, branch: string): Promise<string> {
  const response = await forge.call('GET', `/repos/${fullName(repo)}/git/ref/heads/${branch}`);
  expect(response.status).toBe(200);
  return (response.body as { object: { sha: string } }).object.sha;
}

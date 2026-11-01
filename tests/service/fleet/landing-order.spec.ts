import { expect, stepUntil, test, type ForgeApi, type HubApi, type Repository, type RunnerHost, type Wire } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

const fullName = (repo: Repository) => `${repo.owner}/${repo.name}`;

function graph(repos: Repository[], file = 'LIBRARY.md'): string {
  const branch = `feature/${file.toLowerCase().replace('.md', '')}`;
  const prompt = [
    'import os, pathlib, subprocess',
    `for name in ${JSON.stringify(repos.map((repo) => repo.name))}:`,
    "    path = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name",
    `    subprocess.run(['git', '-C', str(path), 'switch', '-C', ${JSON.stringify(branch)}], check=True)`,
    `    (path / ${JSON.stringify(file)}).write_text('ordered change\\n')`,
    "    subprocess.run(['git', '-C', str(path), 'add', '-A'], check=True)",
    "    subprocess.run(['git', '-C', str(path), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: ordered'], check=True)",
    "    sha = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    `    subprocess.run(['git', '-C', str(path), 'push', 'origin', ${JSON.stringify(branch)}], check=True)`,
    `    subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', ${JSON.stringify(branch)}, '--commit', sha], check=True)`,
  ].join('\n');
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt, judgement: { prompt: "verdict('pass', 'built')\n", choices: { pass: { description: 'Built.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: 600, judgement: { choices: {
      landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
      failure: { description: 'Failure.', to: 'build' }, 'inherited-failure': { description: 'Base failure.', to: 'build' },
    } } },
  } });
}

async function order(api: HubApi, project: string, repos: Repository[]) {
  const response = await api.put(`/api/projects/${project}/landing-order`, { data: { repositories: repos.map((repo) => repo.name) } });
  expect(response.status, JSON.stringify(response.body)).toBe(200);
}

async function lever(forge: ForgeApi, repos: Repository[], armed: boolean) {
  for (const repo of repos) {
    const response = armed
      ? await forge.call('POST', '/_levers/checks_pending', { repo: fullName(repo) })
      : await forge.call('DELETE', `/_levers/checks_pending?repo=${encodeURIComponent(fullName(repo))}`);
    expect(response.status).toBe(200);
  }
}

async function pending(api: HubApi, host: RunnerHost, id: string) {
  const detail = await stepUntil(host, async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body,
    (row) => row.pending !== null || ['done', 'needs_human', 'stopped'].includes(row.status), 80);
  expect(detail.pending).not.toBeNull();
}

async function terms(api: HubApi, id: string) {
  const response = await api.get<Wire.ChunkFlowSubmissionsView>(`/api/chunks/${id}/flow-submissions`);
  expect(response.status).toBe(200);
  return response.body.items.flatMap((item) => item.terms).map(({ repository_id, landing_policy, landing_position }) => ({ repository_id, landing_policy, landing_position }))
    .sort((a, b) => a.repository_id.localeCompare(b.repository_id));
}

async function landed(api: HubApi, host: RunnerHost, id: string, expected: Repository[]) {
  const done = await stepUntil(host, async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body,
    (detail) => ['done', 'needs_human', 'stopped'].includes(detail.status), 80);
  expect([done.status, done.landed]).toEqual(['done', true]);
  const facts: Wire.LandingFactView[] = [];
  let cursor: number | null = 0;
  while (cursor !== null) {
    const page: { status: number; body: { items: Wire.LandingFactView[]; next_cursor: number | null } } =
      await api.get(`/api/chunks/${id}/landing-facts?limit=1&cursor=${cursor}`);
    expect(page.status).toBe(200);
    facts.push(...page.body.items);
    cursor = page.body.next_cursor;
  }
  expect(facts.map((fact) => fact.repo)).toEqual(expected.map(fullName));
  await expect.poll(async () => (await api.get<Wire.ChunkFlowSubmissionsView>(`/api/chunks/${id}/flow-submissions`)).body.items.some((item) => item.closure?.outcome === 'landed')).toBe(true);
  await expect.poll(async () => {
    const submissions = (await api.get<Wire.ChunkFlowSubmissionsView>(`/api/chunks/${id}/flow-submissions`)).body.items;
    const turns: { ended_at: string | null }[] = [];
    for (const submission of submissions) {
      let before: string | null = null;
      do {
        const query: string = before === null ? '' : `?before=${encodeURIComponent(before)}`;
        const page: { status: number; body: { items: { ended_at: string | null }[]; next_before: string | null } } =
          await api.get(`/api/chunks/${id}/flow-submissions/${submission.submission_id}/turns${query}`);
        expect(page.status).toBe(200);
        turns.push(...page.body.items);
        before = page.body.next_before;
      } while (before !== null);
    }
    return turns.length > 0 && turns.every((turn) => turn.ended_at !== null);
  }).toBe(true);
}

async function links(api: HubApi, project: string) {
  const response = await api.get<{ repository_links: { slug: string; position: number | null }[] }>(`/api/projects/${project}/repository-links`);
  expect(response.status).toBe(200);
  return response.body.repository_links.map(({ slug, position }) => [slug, position]);
}

test('test_a_library_ranked_first_lands_before_its_consumer', async ({ repository, issue, project, api, runnerHosts }) => {
  test.setTimeout(180_000);
  const repos = [await repository(), await repository()].sort((a, b) => a.name.localeCompare(b.name));
  const [consumer, library] = repos as [Repository, Repository];
  const host = await runnerHosts.create({ runnerRepositories: { [consumer.name]: consumer.origin!, [library.name]: library.origin! } });
  expect((await api.put(`/api/projects/${project.slug}/landing-order`, { data: { repositories: [library.name, consumer.name] } })).status).toBe(200);
  expect(await links(api, project.slug)).toEqual([[library.name, 1], [consumer.name, 2]]);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph([consumer, library]) } })).status).toBe(201);
  const { chunk } = await issue('LIBRARY.md', { repository: consumer });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const done = await stepUntil(host, async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${chunk.chunk_id}`)).body,
    (detail) => ['done', 'needs_human', 'stopped'].includes(detail.status), 80);
  expect([done.status, done.landed]).toEqual(['done', true]);
  const facts: Wire.LandingFactView[] = [];
  let cursor: number | null = 0;
  while (cursor !== null) {
    const page: { status: number; body: { items: Wire.LandingFactView[]; next_cursor: number | null } } =
      await api.get(`/api/chunks/${chunk.chunk_id}/landing-facts?limit=1&cursor=${cursor}`);
    expect(page.status).toBe(200);
    facts.push(...page.body.items);
    cursor = page.body.next_cursor;
  }
  expect(facts.map((fact) => fact.repo)).toEqual([fullName(library), fullName(consumer)]);
  await landed(api, host, chunk.chunk_id, [library, consumer]);
  const submissions = await api.get<Wire.ChunkFlowSubmissionsView>(`/api/chunks/${chunk.chunk_id}/flow-submissions`);
  expect(submissions.status).toBe(200);
  const libraryRead = await api.get<{ repository_id: string }>(`/api/repositories/${library.name}`);
  const consumerRead = await api.get<{ repository_id: string }>(`/api/repositories/${consumer.name}`);
  expect([libraryRead.status, consumerRead.status]).toEqual([200, 200]);
  expect(submissions.body.items.flatMap((item) => item.terms).map((term) => [term.repository_id, term.landing_policy, term.landing_position]))
    .toEqual(expect.arrayContaining([[libraryRead.body.repository_id, 'merge-on-checks', 1], [consumerRead.body.repository_id, 'merge-on-checks', 2]]));
});

test('test_one_ranked_repository_lands_before_two_unranked_in_slug_order', async ({ repository, issue, project, api, runnerHosts }) => {
  test.setTimeout(180_000);
  const repos = [await repository(), await repository(), await repository()];
  repos.sort((a, b) => a.name.localeCompare(b.name));
  const [first, second, ranked] = repos as [Repository, Repository, Repository];
  const host = await runnerHosts.create({ runnerRepositories: Object.fromEntries(repos.map((repo) => [repo.name, repo.origin!])) });
  await order(api, project.slug, [ranked]);
  expect(await links(api, project.slug)).toEqual([[ranked.name, 1], [first.name, null], [second.name, null]]);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repos, 'RANKED.md') } })).status).toBe(201);
  const { chunk } = await issue('ranked', { repository: first });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await landed(api, host, chunk.chunk_id, [ranked, first, second]);
});

test('test_a_second_project_lands_the_same_repositories_in_its_own_order', async ({ repository, issue, project, api, forge, runnerHosts }) => {
  test.setTimeout(180_000);
  const consumer = await repository();
  const library = await repository();
  const repos = [consumer, library];
  const second = 'consumers';
  const created = await api.post<{ project_id: string }>('/api/projects', { data: { slug: second, name: 'Consumers', description: '' } });
  expect(created.status).toBe(201);
  expect((await api.put(`/api/projects/${second}/source-links/${library.name}`)).status).toBe(200);
  for (const repo of repos) expect((await api.put(`/api/projects/${second}/repository-links/${repo.name}`)).status).toBe(200);
  await order(api, project.slug, [library, consumer]);
  await order(api, second, [consumer, library]);
  const host = await runnerHosts.create({
    runnerRepositories: Object.fromEntries(repos.map((repo) => [repo.name, repo.origin!])),
    runnerConfig: { runner: [{ name: 'runner-local', projects: [{ project: project.slug, workspace: 'workspace-local' }, { project: second, workspace: 'workspace-local' }] }] },
  });
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repos, 'FIRST.md') } })).status).toBe(201);
  const first = (await issue('first project', { repository: consumer })).chunk.chunk_id;
  expect((await api.post(`/api/chunks/${first}/promote`)).status).toBe(202);
  await landed(api, host, first, [library, consumer]);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repos, 'SECOND.md') } })).status).toBe(201);
  const filed = await forge.createIssue(fullName(library), { title: 'second project' });
  const ingest = await api.post<{ chunk_id: string; project_id: string }>('/api/chunks', { data: { tokens: [`${library.tokenPrefix}:${filed.number}`], project: second } });
  expect(ingest.status).toBe(201);
  expect(ingest.body.project_id).toBe(created.body.project_id);
  expect((await api.post(`/api/chunks/${ingest.body.chunk_id}/promote`)).status).toBe(202);
  await landed(api, host, ingest.body.chunk_id, [consumer, library]);
});

test.describe('restart preserves captured terms', () => {
  test.use({ isolation: 'fresh-hub' });
  test('test_an_interrupted_turn_keeps_the_terms_its_first_turn_took', async ({ repository, issue, project, api, forge, runnerHosts, freshHub }) => {
    test.setTimeout(180_000);
    const consumer = await repository();
    const library = await repository();
    const repos = [consumer, library];
    const host = await runnerHosts.create({ runnerRepositories: Object.fromEntries(repos.map((repo) => [repo.name, repo.origin!])) });
    await order(api, project.slug, [library, consumer]);
    await lever(forge, repos, true);
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repos, 'INTERRUPTED.md') } })).status).toBe(201);
    const { chunk } = await issue('interrupted', { repository: consumer });
    const id = chunk.chunk_id;
    expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
    await pending(api, host, id);
    const taken = await terms(api, id);
    expect(taken.map((term) => [term.landing_policy, term.landing_position]).sort()).toEqual([['merge-on-checks', 1], ['merge-on-checks', 2]]);
    await order(api, project.slug, [consumer, library]);
    expect(await links(api, project.slug)).toEqual([[consumer.name, 1], [library.name, 2]]);
    await freshHub.stop();
    await lever(forge, repos, false);
    await freshHub.start();
    await landed(api, host, id, [library, consumer]);
    expect(await terms(api, id)).toEqual(taken);
  });
});

test('test_a_reorder_while_a_chunk_waits_behind_another_reaches_only_later_chunks', async ({ repository, issue, project, api, forge, runnerHosts }) => {
  test.setTimeout(240_000);
  const consumer = await repository();
  const library = await repository();
  const repos = [consumer, library];
  const host = await runnerHosts.create({ runnerRepositories: Object.fromEntries(repos.map((repo) => [repo.name, repo.origin!])) });
  await order(api, project.slug, [library, consumer]);
  await lever(forge, repos, true);
  async function chunk(file: string) {
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repos, file) } })).status).toBe(201);
    const created = await issue(file, { repository: consumer });
    const id = created.chunk.chunk_id;
    expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
    return id;
  }
  const ahead = await chunk('AHEAD.md');
  await pending(api, host, ahead);
  const behind = await chunk('BEHIND.md');
  await pending(api, host, behind);
  const aheadTerms = await terms(api, ahead);
  const behindTerms = await terms(api, behind);
  expect(aheadTerms).toEqual(behindTerms);
  const aheadSubmissions = (await api.get<Wire.ChunkFlowSubmissionsView>(`/api/chunks/${ahead}/flow-submissions`)).body.items;
  const behindSubmissions = (await api.get<Wire.ChunkFlowSubmissionsView>(`/api/chunks/${behind}/flow-submissions`)).body.items;
  expect(aheadSubmissions).toHaveLength(1);
  expect(behindSubmissions).toHaveLength(1);
  expect(behindSubmissions[0]!.arrival).toBeGreaterThan(aheadSubmissions[0]!.arrival);
  expect(behindSubmissions[0]!.flow_ids.slice().sort()).toEqual(aheadSubmissions[0]!.flow_ids.slice().sort());
  expect(aheadSubmissions[0]!.flow_ids).toHaveLength(2);
  await order(api, project.slug, [consumer, library]);
  await lever(forge, repos, false);
  await landed(api, host, ahead, [library, consumer]);
  await landed(api, host, behind, [library, consumer]);
  expect(await terms(api, ahead)).toEqual(aheadTerms);
  expect(await terms(api, behind)).toEqual(behindTerms);
  const later = await chunk('LATER.md');
  await landed(api, host, later, [consumer, library]);
});

test('test_a_relinked_repository_keeps_its_position', async ({ repository, issue, project, api, runnerHosts }) => {
  test.setTimeout(180_000);
  const consumer = await repository();
  const library = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [consumer.name]: consumer.origin!, [library.name]: library.origin! } });
  await order(api, project.slug, [library, consumer]);
  expect((await api.post(`/api/projects/${project.slug}/repository-links/${library.name}/retire`)).status).toBe(200);
  expect(await links(api, project.slug)).toEqual([[consumer.name, 2]]);
  expect((await api.put(`/api/projects/${project.slug}/repository-links/${library.name}`)).status).toBe(200);
  expect(await links(api, project.slug)).toEqual([[library.name, 1], [consumer.name, 2]]);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph([consumer, library], 'RELINKED.md') } })).status).toBe(201);
  const { chunk } = await issue('relinked', { repository: consumer });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await landed(api, host, chunk.chunk_id, [library, consumer]);
});

test('test_an_exhausted_gap_is_renumbered_and_the_order_lands', async ({ repository, issue, project, api, runnerHosts }) => {
  test.setTimeout(180_000);
  const first = await repository();
  let still = await repository();
  let moving = await repository();
  const repos = [first, still, moving];
  const host = await runnerHosts.create({ runnerRepositories: Object.fromEntries(repos.map((repo) => [repo.name, repo.origin!])) });
  await order(api, project.slug, repos);
  let halvings = 0;
  let renumbered = false;
  while (halvings < 200) {
    const response = await api.post(`/api/projects/${project.slug}/landing-order/position`, { data: { repository: moving.name, after_repository: first.name } });
    expect(response.status).toBe(200);
    const current = await links(api, project.slug);
    if (halvings > 0 && current[1]![1] === 1.5) {
      expect(current).toEqual([[first.name, 1], [moving.name, 1.5], [still.name, 2]]);
      renumbered = true;
      break;
    }
    [moving, still] = [still, moving];
    halvings++;
  }
  expect(renumbered).toBe(true);
  expect(halvings).toBeGreaterThan(40);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repos, 'RENUMBERED.md') } })).status).toBe(201);
  const { chunk } = await issue('renumbered', { repository: first });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await landed(api, host, chunk.chunk_id, [first, moving, still]);
});

test('test_an_exported_document_applies_back_writing_nothing', async ({ repository, project, api }) => {
  const consumer = await repository();
  const library = await repository();
  const unranked = await repository();
  await order(api, project.slug, [library, consumer]);
  const exported = await api.get<Wire.ConfigDocument>('/api/config/export');
  expect(exported.status).toBe(200);
  expect(exported.body.projects?.find((row) => row.slug === project.slug)?.landing_order).toEqual([library.name, consumer.name]);
  const applied = await api.post<{ outcomes: { op: string }[] }>('/api/config/apply', { data: exported.body });
  expect(applied.status).toBe(200);
  expect(applied.body.outcomes.filter((outcome) => outcome.op !== 'unchanged')).toEqual([]);
  expect(await links(api, project.slug)).toEqual([[library.name, 1], [consumer.name, 2], [unranked.name, null]]);
});

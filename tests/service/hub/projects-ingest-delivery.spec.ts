import { expect, test, type Wire } from '../../harness/fixtures';

// exemplar: bzh:service-fixtures-only, bzh:service-clients-only, bzh:service-isolation-earned.

// A tenant holding two projects, each drawing from its own work source: an ingest that names no project mints into the
// one its sources link, and one its sources leave open is refused whole, naming the projects to choose from. A chunk
// whose commit names a repository its project does not link reaches its `deliver` step's `land` action, which is
// refused before the action runs — nothing reaches the forge — with a critical `repository-not-linked` event, and the
// step's failure route taken. Every test creates a project and a work source, both tenant-wide, and one asserts the
// tenant's chunk list is empty, so each owns its tenant.
test.use({ isolation: 'fresh-tenant' });

/** `build` hands a commit to a `deliver` hub step taking the `land` action, whose `failure` routes to `triage` — a node nothing else reaches. */
const NOT_LINKED_GRAPH_YAML = `name: not-linked-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: '# build'
    judgement:
      prompt: '# judge'
      choices:
        pass:
          description: green
          to: deliver
    retries:
      max: 1
      exhausted: escalate
  deliver:
    executor: hub
    action: land
    poll_interval: 1
    judgement:
      choices:
        landed:
          description: Landed.
          to: done
        conflict:
          description: Conflict.
          to: build
        failure:
          description: Failed to deliver.
          to: triage
        inherited-failure:
          description: The base check stays red.
          to: build
  triage:
    executor: runner
    prompt: '# triage'
    judgement:
      prompt: '# judge'
      choices:
        pass:
          description: seen
          to: done
    retries:
      max: 1
      exhausted: escalate
`;

/**
 * The tenant's `default` project links the repository `repository()` makes and its source; a `second` project links
 * only a source of its own over another forge repository — no repository. Returns `default`'s repository, and
 * `second`'s source slug and repository name.
 */
async function twoProjects({ api, forge, repository }: Pick<Parameters<Parameters<typeof test>[2]>[0], 'api' | 'forge' | 'repository'>) {
  const defaults = await repository();
  const created = await api.post('/api/projects', { data: { slug: 'second', name: 'Second' } });
  expect(created.status).toBe(201);
  const name = defaults.name.replace(/^r-/, 's-');
  await forge.createRepository('blizzard', name);
  const secret = await api.post('/api/secrets', { data: { name: 'second-forge-token', value: 'fixture-token' } });
  expect(secret.status).toBe(201);
  const source = {
    slug: name,
    provider: 'github',
    locator: `blizzard/${name}`,
    api_base: forge.url,
    secret: 'second-forge-token',
  } satisfies Wire.WorkSourceDocument;
  const added = await api.post('/api/work-sources', { data: source });
  expect(added.status).toBe(201);
  const linked = await api.put(`/api/projects/second/source-links/${name}`);
  expect(linked.status).toBe(200);
  return { defaults, secondSource: name, secondRepo: name };
}

test('test_an_ingest_naming_no_project_mints_into_the_project_its_source_links', async ({ api, forge, repository }) => {
  const { defaults, secondSource, secondRepo } = await twoProjects({ api, forge, repository });
  const intoDefault = await forge.createIssue(`blizzard/${defaults.name}`, { title: 'into default', body: 'the chunk' });
  const intoSecond = await forge.createIssue(`blizzard/${secondRepo}`, { title: 'into second', body: 'the chunk' });

  const first = await api.post('/api/chunks', { data: { tokens: [`${defaults.name}:${intoDefault.number}`] } });
  const second = await api.post('/api/chunks', { data: { tokens: [`${secondSource}:${intoSecond.number}`] } });
  expect(first.status).toBe(201);
  expect(second.status).toBe(201);

  const landedFirst = await api.get(`/api/chunks/${first.body.chunk_id}`);
  const landedSecond = await api.get(`/api/chunks/${second.body.chunk_id}`);
  expect([landedFirst.body.project_slug, landedFirst.body.project_name]).toEqual(['default', 'Default']);
  expect([landedSecond.body.project_slug, landedSecond.body.project_name]).toEqual(['second', 'Second']);
  expect(landedFirst.body.project_id).not.toBe(landedSecond.body.project_id);
});

test('test_an_ingest_whose_sources_disagree_is_refused_whole_naming_the_projects', async ({ api, forge, repository }) => {
  const { defaults, secondSource, secondRepo } = await twoProjects({ api, forge, repository });
  const one = await forge.createIssue(`blizzard/${defaults.name}`, { title: 'one', body: 'the chunk' });
  const two = await forge.createIssue(`blizzard/${secondRepo}`, { title: 'two', body: 'the chunk' });

  const refused = await api.post('/api/chunks', { data: { tokens: [`${defaults.name}:${one.number}`, `${secondSource}:${two.number}`] } });

  expect(refused.status).toBe(422);
  expect(refused.body.detail.code).toBe('project_required');
  expect(refused.body.detail.projects.map((project: { slug: string }) => project.slug).sort()).toEqual(['default', 'second']);
  const listed = await api.get('/api/chunks');
  expect(listed.status).toBe(200);
  expect(listed.body.chunks).toEqual([]);
});

test('test_a_commit_its_project_does_not_link_is_refused_at_deliver_before_the_land_action', async ({ api, forge, repository, mockRunner }) => {
  const { defaults, secondSource, secondRepo } = await twoProjects({ api, forge, repository });
  const minted = await api.post('/api/graphs', { data: { definition_yaml: NOT_LINKED_GRAPH_YAML } });
  expect(minted.status).toBe(201);
  const triage = (minted.body.nodes as { name: string; node_id: string }[]).find((node) => node.name === 'triage')?.node_id;
  expect(triage).toBeDefined();
  const issue = await forge.createIssue(`blizzard/${secondRepo}`, { title: 'unlinked land', body: 'the chunk' });
  const ingested = await api.post('/api/chunks', { data: { tokens: [`${secondSource}:${issue.number}`] } });
  expect(ingested.status).toBe(201);
  const chunkId = ingested.body.chunk_id as string;
  expect((await api.get(`/api/chunks/${chunkId}`)).body.project_slug).toBe('second');
  const repinned = await api.patch(`/api/chunks/${chunkId}`, { data: { graph_id: minted.body.graph_id } });
  expect(repinned.status).toBe(202);
  const promoted = await api.post(`/api/chunks/${chunkId}/promote`);
  expect(promoted.status).toBe(202);

  const runner = await mockRunner('runner-second', { projects: ['second'] });
  expect((await runner.drive('register')).body.status).toBe(201);
  const claimed = await runner.drive('claim', { chunk_id: chunkId });
  expect(claimed.body.claimed).toBe(true);
  const commit = { name: 'commit', kind: 'git_commit', repo: defaults.name, branch_name: 'bz/not-linked', commit_hash: 'a'.repeat(40) };
  const completed = await runner.drive('complete', { chunk_id: chunkId, choice: 'pass', artifacts: [commit] });
  expect(completed.body.response?.outcome).toBe('hub_node_taken');

  await expect
    .poll(async () => (await api.get(`/api/chunks/${chunkId}`)).body.current_node_id, { timeout: 30_000 })
    .toBe(triage);

  const events = await api.get('/api/events', { params: { chunk_id: chunkId } });
  expect(events.status).toBe(200);
  const refusals = (events.body.events as { kind: string; severity: string; message: string }[]).filter((event) => event.kind === 'repository-not-linked');
  expect(refusals).toHaveLength(1);
  expect(refusals[0]?.severity).toBe('critical');
  expect(refusals[0]?.message).toContain(`repository record ${defaults.name}`);
  expect(refusals[0]?.message).toContain('project second');
  const artifacts = ((await api.get(`/api/chunks/${chunkId}`)).body.artifacts as { name: string }[]).map((artifact) => artifact.name);
  expect(artifacts).toContain('hub-log.repository-not-linked');
  const pulls = await forge.call('GET', `/repos/blizzard/${defaults.name}/pulls?state=all`);
  expect(pulls.status).toBe(200);
  expect(pulls.body).toEqual([]);
});

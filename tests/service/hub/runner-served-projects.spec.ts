import { expect, test } from '../../harness/fixtures';

// The projects a runner serves, over a real hub: what adding and registering store, what the matched peek then offers,
// and how a claim of an unserved project's chunk is refused. Each test mints a runner and reads the tenant's project
// count or its whole matched queue, so each owns its tenant.
test.use({ isolation: 'fresh-tenant' });

/** Satisfies a chunk with no declared harness preference, via its `default` binding. */
const CAPABILITIES = [{ harness_id: 'claude', default: true }];

/**
 * The incompatible-denial 409 body as a runner built before projects reads it, restated field for field rather than
 * taken from the generated wire types, which follow the live model: a change to the live model cannot hide a break in
 * what an older runner parses.
 */
interface PreSliceIncompatibleDenial {
  chunk_id: string;
  incompatible_runner_id: string;
  detail: string;
}

/** Which outcome a runner built before projects reads a claim's 409 body as: its discriminators, in its order. */
function preSliceReading(body: Record<string, unknown>): string {
  if ('status' in body) return 'terminal';
  if ('prerequisite_chunk_id' in body) return 'dependency';
  if ('incompatible_runner_id' in body) return 'incompatible';
  return 'conflict';
}

interface ServedView {
  entry: string;
  project_id: string | null;
  slug: string | null;
}

const servedIds = (views: ServedView[]) => views.map((view) => view.project_id);

test('test_a_registration_naming_no_projects_keeps_the_declaration_and_an_empty_list_serves_nothing', async ({
  api,
  fleet,
  issue,
  project,
}) => {
  const { chunk } = await issue('a chunk of the served project');
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const runner = await fleet('declared', { projects: ['default'] });

  const register = async (declared: { projects?: string[] } = {}) => {
    const registered = await runner.post('/api/fleet/runners', {
      data: { workspace_id: 'w1', capabilities: CAPABILITIES, ...declared },
    });
    expect(registered.status).toBe(201);
    return registered.body;
  };
  const registryServed = async (): Promise<ServedView[]> => {
    const listed = await api.get('/api/runners');
    expect(listed.status).toBe(200);
    const mine = listed.body.runners.find((candidate: { runner_id: string }) => candidate.runner_id === runner.runnerId);
    return mine.projects;
  };
  const offered = async (): Promise<string[]> => {
    const peek = await runner.post('/api/fleet/queue/peek', { data: { capabilities: CAPABILITIES } });
    expect(peek.status).toBe(200);
    return peek.body.entries.map((entry: { chunk_id: string }) => entry.chunk_id);
  };

  const kept = await register();
  expect(kept.projects.map((view: ServedView) => [view.entry, view.project_id, view.slug])).toEqual([['default', project.id, 'default']]);
  expect(servedIds(await registryServed())).toEqual([project.id]);
  expect(await offered()).toEqual([chunk.chunk_id]);

  const cleared = await register({ projects: [] });
  expect(cleared.projects).toEqual([]);
  expect(await registryServed()).toEqual([]);
  expect(await offered()).toEqual([]);

  // Naming no projects again keeps the empty declaration the hub now holds.
  expect((await register()).projects).toEqual([]);
  expect(await offered()).toEqual([]);
});

test('test_adding_a_runner_naming_no_projects_serves_a_one_project_tenants_project_by_id', async ({ api, fleet, project }) => {
  const runner = await fleet('sole-default');

  const listed = await api.get('/api/runners');
  const mine = listed.body.runners.find((candidate: { runner_id: string }) => candidate.runner_id === runner.runnerId);
  expect(mine.projects.map((view: ServedView) => [view.entry, view.project_id, view.slug])).toEqual([[project.id, project.id, 'default']]);

  const registered = await runner.post('/api/fleet/runners', { data: { workspace_id: 'w1', capabilities: CAPABILITIES } });
  expect(registered.status).toBe(201);
  expect(servedIds(registered.body.projects)).toEqual([project.id]);
});

test('test_adding_a_runner_naming_no_projects_serves_nothing_on_a_two_project_tenant', async ({ api, fleet }) => {
  const created = await api.post('/api/projects', { data: { slug: 'beta', name: 'Beta' } });
  expect(created.status).toBe(201);
  const runner = await fleet('no-default');

  const listed = await api.get('/api/runners');
  const mine = listed.body.runners.find((candidate: { runner_id: string }) => candidate.runner_id === runner.runnerId);
  expect(mine.projects).toEqual([]);

  const registered = await runner.post('/api/fleet/runners', { data: { workspace_id: 'w1', capabilities: CAPABILITIES } });
  expect(registered.status).toBe(201);
  expect(registered.body.projects).toEqual([]);
});

test('test_claiming_an_unserved_projects_chunk_is_a_project_denial_an_older_runner_reads_as_incompatible', async ({
  api,
  fleet,
  issue,
}) => {
  const { chunk } = await issue('a chunk of the default project');
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const created = await api.post('/api/projects', { data: { slug: 'beta', name: 'Beta' } });
  expect(created.status).toBe(201);
  const beta = created.body.project_id as string;
  const runner = await fleet('beta-only', { projects: ['beta'] });
  const registered = await runner.post('/api/fleet/runners', { data: { workspace_id: 'w1', capabilities: CAPABILITIES } });
  expect(registered.status).toBe(201);
  expect(servedIds(registered.body.projects)).toEqual([beta]);

  const claim = await runner.post('/api/fleet/routes', {
    data: { chunk_id: chunk.chunk_id, workspace_id: 'w1', environment_ids: ['env-a'] },
  });

  expect(claim.status).toBe(409);
  const body = claim.body as Record<string, unknown>;
  expect([body.chunk_id, body.incompatible_runner_id, body.reason]).toEqual([chunk.chunk_id, runner.runnerId, 'project']);
  expect(body.detail).toContain('default');
  expect(preSliceReading(body)).toBe('incompatible');
  const denial = body as unknown as PreSliceIncompatibleDenial;
  expect(typeof denial.chunk_id).toBe('string');
  expect(typeof denial.incompatible_runner_id).toBe('string');
  expect(typeof denial.detail).toBe('string');
  expect((await api.get(`/api/chunks/${chunk.chunk_id}`)).body.route).toBeNull();
});

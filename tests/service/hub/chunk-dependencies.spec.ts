import { expect, test, RUNNER_VERSION } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('releasing a dependency removes the block and permits a claim', async ({ api, fleet, issue }) => {
  const dependent = (await issue('dependent to release')).chunk.chunk_id;
  const prerequisite = (await issue('prerequisite to release')).chunk.chunk_id;
  expect((await api.post(`/api/chunks/${dependent}/promote`)).status).toBe(202);
  const path = `/api/chunks/${dependent}/dependencies`;
  const data = { prerequisite_chunk_id: prerequisite };
  const declared = await api.post(path, { data: { ...data, by: 'alice' } });
  expect(declared.status).toBe(202);
  expect(declared.body).toMatchObject({ dependent_chunk_id: dependent, prerequisite_chunk_id: prerequisite, declared_by: 'alice', released_at: null });
  const replay = await api.post(path, { data: { ...data, by: 'bob' } });
  expect(replay.status).toBe(202);
  expect(replay.body.dependency_id).toBe(declared.body.dependency_id);

  const runner = await fleet('dependency-release');
  expect((await runner.post('/api/fleet/runners', { data: { version: RUNNER_VERSION, workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] } })).status).toBe(201);
  const claim = () => runner.post('/api/fleet/routes', { data: { chunk_id: dependent, workspace_id: 'w1', environment_ids: [] } });
  const blocked = await claim();
  expect(blocked.status).toBe(409);
  expect(blocked.headers['content-type']).toContain('application/problem+json');
  expect(blocked.body).toMatchObject({ type: 'urn:blizzard:problem:claim-dependency', chunk_id: dependent, prerequisite_chunk_id: prerequisite });
  expect((await api.get(`/api/chunks/${dependent}`)).body.route).toBeNull();

  const released = await api.post(`${path}/release`, { data: { ...data, by: 'carol' } });
  expect(released.status).toBe(202);
  expect(released.body).toMatchObject({ dependency_id: declared.body.dependency_id, released_by: 'carol' });
  expect(released.body.released_at).toBeTruthy();
  const repeated = await api.post(`${path}/release`, { data: { ...data, by: 'dave' } });
  expect(repeated.status).toBe(409);
  expect(repeated.body).toMatchObject({ type: 'urn:blizzard:problem:no-standing-dependency', dependent_chunk_id: dependent, prerequisite_chunk_id: prerequisite });
  expect((await api.get(`/api/chunks/${dependent}`)).body.neighborhood.prerequisites).toEqual([]);
  expect((await api.get(`/api/chunks/${prerequisite}`)).body.neighborhood.dependents).toEqual([]);
  expect((await claim()).status).toBe(201);
  expect((await api.get(`/api/chunks/${dependent}`)).body.status).toBe('running');
});

test('cycle and ephemeral prerequisite refusals leave the standing neighborhood unchanged', async ({ api, issue }) => {
  const dependent = (await issue('cycle dependent')).chunk.chunk_id;
  const prerequisite = (await issue('cycle prerequisite')).chunk.chunk_id;
  const deleted = (await issue('deleted prerequisite')).chunk.chunk_id;
  const declare = (id: string, prerequisiteId: string) => api.post(`/api/chunks/${id}/dependencies`, { data: { prerequisite_chunk_id: prerequisiteId } });
  expect((await declare(dependent, prerequisite)).status).toBe(202);
  const before = (await api.get(`/api/chunks/${dependent}`)).body.neighborhood;

  const cycle = await declare(prerequisite, dependent);
  expect(cycle.status).toBe(409);
  expect(cycle.body).toMatchObject({ type: 'urn:blizzard:problem:dependency-would-close-cycle', dependent_chunk_id: prerequisite, prerequisite_chunk_id: dependent });
  expect((await api.delete(`/api/chunks/${deleted}`, { data: {} })).status).toBe(202);
  const ephemeral = await declare(dependent, deleted);
  expect(ephemeral.status).toBe(409);
  expect(ephemeral.body).toMatchObject({ type: 'urn:blizzard:problem:prerequisite-ephemeral', chunk_id: deleted });
  expect((await api.get(`/api/chunks/${dependent}`)).body.neighborhood).toEqual(before);
});

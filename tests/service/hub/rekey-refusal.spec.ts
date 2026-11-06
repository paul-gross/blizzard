import { expect, test, RUNNER_VERSION } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('rekey without a route is refused without mutation and succeeds after a claim', async ({ api, fleet, issue }) => {
  const graph = JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: { build: { executor: 'runner', prompt: '# build', judgement: { prompt: '# judge', choices: { pass: { description: 'done', to: 'done' } } }, retries: { max: 1, exhausted: 'escalate' } } } });
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph } })).status).toBe(201);
  const { chunk } = await issue('rekey refusal');
  const id = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  const runner = await fleet('rekey-refusal');
  const path = `/api/fleet/chunks/${id}/route-token`;
  const before = (await api.get(`/api/chunks/${id}`)).body;
  const refused = await runner.post(path);
  expect(refused.status).toBe(404);
  expect(refused.headers['content-type']).toContain('application/problem+json');
  expect(refused.body).toMatchObject({ type: 'urn:blizzard:problem:route-not-found', status: 404, instance: path, detail: `chunk ${id} has no live route` });
  const after = (await api.get(`/api/chunks/${id}`)).body;
  expect(after.status).toBe(before.status);
  expect(after.current_node_id).toBe(before.current_node_id);
  expect(after.route).toEqual(before.route);
  expect(after.latest_epoch).toBe(before.latest_epoch);

  expect((await runner.post('/api/fleet/runners', { data: { version: RUNNER_VERSION, workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] } })).status).toBe(201);
  expect((await runner.post('/api/fleet/routes', { data: { chunk_id: id, workspace_id: 'w1', environment_ids: [] } })).status).toBe(201);
  expect((await runner.post(path)).status).toBe(200);
});

import { expect, test, RUNNER_VERSION } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

const graph = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: '# build',
      judgement: { prompt: '# judge', choices: { pass: { description: 'done', to: 'done' } } },
      retries: { max: 1, exhausted: 'escalate' },
    },
  },
});

test('stale release is a problem, malformed release is validation, and neither changes the route', async ({ api, fleet, issue }) => {
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph } })).status).toBe(201);
  const { chunk } = await issue('release refusal over the wire');
  const id = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  const runner = await fleet('release-problems');
  expect((await runner.post('/api/fleet/runners', { data: { version: RUNNER_VERSION, workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] } })).status).toBe(201);
  const claim = await runner.post('/api/fleet/routes', { data: { chunk_id: id, workspace_id: 'w1', environment_ids: [] } });
  expect(claim.status).toBe(201);
  const path = `/api/fleet/chunks/${id}/release`;
  const before = (await api.get(`/api/chunks/${id}`)).body;
  expect(before.status).toBe('running');

  const stale = await runner.post(path, { data: { epoch: 0, route_token: claim.body.route_token, reason: 'throttled' } });
  expect(stale.status).toBe(409);
  expect(stale.headers['content-type']).toContain('application/problem+json');
  expect(stale.body).toMatchObject({ type: 'urn:blizzard:problem:release-refusal', instance: path, status: 409, chunk_id: id, reason: 'stale' });
  expect(typeof stale.body.detail).toBe('string');

  const malformed = await runner.post(path, { data: { epoch: 1, route_token: claim.body.route_token, reason: '' } });
  expect(malformed.status).toBe(422);
  expect(malformed.headers['content-type']).toContain('application/json');
  expect(Array.isArray(malformed.body.detail)).toBe(true);
  expect(malformed.body.type).toBeUndefined();
  const after = (await api.get(`/api/chunks/${id}`)).body;
  expect(after.status).toBe(before.status);
  expect(after.current_node_id).toBe(before.current_node_id);
  expect(after.route).toEqual(before.route);
  expect(after.latest_epoch).toBe(before.latest_epoch);
});

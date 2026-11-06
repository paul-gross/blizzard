import { expect, test, RUNNER_VERSION } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('claim refusals preserve problem and legacy fields without routing the chunk', async ({ api, fleet, issue }) => {
  const { chunk } = await issue('claim problem refusals');
  const id = chunk.chunk_id;
  const runner = await fleet('claim-problems');
  const claim = () => runner.post('/api/fleet/routes', { data: { chunk_id: id, workspace_id: 'w1', environment_ids: [] } });

  const unregistered = await claim();
  expect(unregistered.status).toBe(403);
  expect(unregistered.headers['content-type']).toContain('application/problem+json');
  expect(unregistered.body).toMatchObject({ type: 'urn:blizzard:problem:claim-runner-unregistered', instance: '/api/fleet/routes', status: 403, chunk_id: id, runner_id: runner.runnerId });

  expect((await runner.post('/api/fleet/runners', { data: { version: RUNNER_VERSION, workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] } })).status).toBe(201);
  const notReady = await claim();
  expect(notReady.status).toBe(409);
  expect(notReady.headers['content-type']).toContain('application/problem+json');
  expect(notReady.body).toMatchObject({ type: 'urn:blizzard:problem:claim-not-ready', instance: '/api/fleet/routes', status: 'not_ready', chunk_id: id });
  const malformed = await runner.post('/api/fleet/routes', { data: { chunk_id: [] } });
  expect(malformed.status).toBe(422);
  expect(malformed.headers['content-type']).toContain('application/json');
  expect(Array.isArray(malformed.body.detail)).toBe(true);
  expect(malformed.body.type).toBeUndefined();
  expect((await api.get(`/api/chunks/${id}`)).body.route).toBeNull();
});

test('runner reads report missing resources as typed problems', async ({ fleet }) => {
  const runner = await fleet('missing-resources');
  const missingChunk = await runner.get('/api/fleet/chunks/ch_missing/garden/proposals/page');
  expect(missingChunk.status).toBe(404);
  expect(missingChunk.headers['content-type']).toContain('application/problem+json');
  expect(missingChunk.body).toMatchObject({
    type: 'urn:blizzard:problem:chunk-not-found',
    instance: '/api/fleet/chunks/ch_missing/garden/proposals/page',
    status: 404,
  });
  const missingArtifact = await runner.get('/api/fleet/system-artifacts/missing/ghost?secret=hidden');
  expect(missingArtifact.status).toBe(404);
  expect(missingArtifact.headers['content-type']).toContain('application/problem+json');
  expect(missingArtifact.body).toMatchObject({
    type: 'urn:blizzard:problem:system-artifact-not-found',
    instance: '/api/fleet/system-artifacts/missing/ghost',
    status: 404,
  });
  const missingQuestion = await runner.get('/api/fleet/questions/q_missing');
  expect(missingQuestion.status).toBe(404);
  expect(missingQuestion.headers['content-type']).toContain('application/problem+json');
  expect(missingQuestion.body).toMatchObject({
    type: 'urn:blizzard:problem:question-not-found',
    instance: '/api/fleet/questions/q_missing',
    status: 404,
  });
});

test('runner heartbeat refuses a different runner as a typed problem', async ({ fleet }) => {
  const runner = await fleet('heartbeat-owner');
  const other = await fleet('heartbeat-other');
  expect((await runner.post('/api/fleet/runners', { data: { version: RUNNER_VERSION, workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] } })).status).toBe(201);
  const path = `/api/fleet/runners/${other.runnerId}/heartbeats`;
  const refused = await runner.post(`${path}?secret=hidden`);
  expect(refused.status).toBe(403);
  expect(refused.headers['content-type']).toContain('application/problem+json');
  expect(refused.body).toMatchObject({
    type: 'urn:blizzard:problem:runner-ownership',
    status: 403,
    instance: path,
    detail: `token belongs to runner '${runner.runnerId}', not '${other.runnerId}'`,
  });
  expect((await runner.post(`/api/fleet/runners/${runner.runnerId}/heartbeats`)).status).toBe(204);
});

test('runner heartbeat before registration returns a typed not-found problem', async ({ fleet }) => {
  const runner = await fleet('heartbeat-unregistered');
  const path = `/api/fleet/runners/${runner.runnerId}/heartbeats`;
  const refused = await runner.post(`${path}?secret=hidden`);
  expect(refused.status).toBe(404);
  expect(refused.headers['content-type']).toContain('application/problem+json');
  expect(refused.body).toMatchObject({
    type: 'urn:blizzard:problem:runner-not-found',
    title: 'Runner not found',
    status: 404,
    instance: path,
    detail: `unknown runner ${runner.runnerId}`,
  });
  expect((await runner.post('/api/fleet/runners', { data: { version: RUNNER_VERSION, workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] } })).status).toBe(201);
  expect((await runner.post(path)).status).toBe(204);
});

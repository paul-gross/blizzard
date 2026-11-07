import { expect, stepChunkUntil, stepUntilHeld, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('a host cannot retire its runner while a route is held, but can after the work completes', async ({ api, bearer, issue, runnerHost, workerCheckpoint }) => {
  const graph = JSON.stringify({
    name: 'default-delivery',
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        prompt: workerCheckpoint.snippet('hold'),
        judgement: { prompt: "verdict('pass', 'done')\n", choices: { pass: { description: 'done', to: 'done' } } },
        retries: { max: 1, exhausted: 'escalate' },
      },
    },
  });
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph } })).status).toBe(201);
  const { chunk } = await issue('hold a route during host runner retirement');
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await stepUntilHeld(runnerHost, chunk.chunk_id, workerCheckpoint, 'hold');

  const runner = runnerHost.runners['runner-local']!;
  const host = await bearer(runnerHost.hubTokens().host!);
  const path = `/api/fleet/host/runners/${runner.id}/retire`;
  const refused = await host.post(path, { data: { by: 'operator', force: false } });
  expect(refused.status).toBe(409);
  expect(refused.headers['content-type']).toMatch(/^application\/problem\+json/);
  expect(refused.body).toMatchObject({
    type: 'urn:blizzard:problem:runner-holds-routes', title: 'Runner holds routes', status: 409, instance: path,
  });
  expect(refused.body.detail).toContain(`runner ${runner.id} holds 1 chunk(s): ${chunk.chunk_id}`);
  expect(refused.body.detail).toContain('retire with --force to release');
  expect((await api.get(`/api/runners/${runner.id}`)).body.retired).toBe(false);

  workerCheckpoint.release('hold');
  await stepChunkUntil(runnerHost, api, chunk.chunk_id, (status) => status === 'done');
  const retired = await host.post(path, { data: { by: 'operator', force: false } });
  expect(retired.status, JSON.stringify(retired.body)).toBe(200);
  expect((await api.get(`/api/runners/${runner.id}`)).body.retired).toBe(true);
});

import { expect, stepChunkUntil, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant', claimBarrierEnabled: true });

test('the runner absorbs a real hub claim refusal without a lease and recovers', async ({ api, claimBarrier, issue, runnerHost }) => {
  const runner = runnerHost.runners['runner-local']!;
  expect((await runnerHost.step()).outcome).toBe('completed');
  const graph = { name: 'default-delivery', entry: 'build', nodes: { build: { executor: 'runner', prompt: "print('worked')\n", judgement: { prompt: "verdict('pass', 'done')\n", choices: { pass: { description: 'done', to: 'done' } } } } } };
  expect((await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(graph) } })).status).toBe(201);
  const { chunk } = await issue('claim refusal and recovery');
  const id = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  const barrier = claimBarrier!;
  barrier.arm(id);
  let arrived = false;
  const arrival = barrier.arrived().then(() => { arrived = true; });
  const drive = (async () => {
    for (let i = 0; i < 30 && !arrived; i++) expect((await runnerHost.step()).outcome).toBe('completed');
  })();
  await Promise.race([arrival, drive]);
  expect(arrived, 'runner did not attempt the chunk claim').toBe(true);
  expect((await api.post(`/api/chunks/${id}/pause`, { data: { by: 'operator' } })).status).toBe(202);
  barrier.release();
  const refusal = await barrier.response();
  expect(refusal.status).toBe(409);
  expect(refusal.contentType).toContain('application/problem+json');
  expect(refusal.body).toMatchObject({ type: 'urn:blizzard:problem:claim-not-ready', status: 'paused', chunk_id: id, instance: '/api/fleet/routes' });
  await drive;
  expect((await api.get(`/api/chunks/${id}`)).body.route).toBeNull();
  expect((await runner.leases()).body.items.filter((lease) => lease.chunk_id === id)).toEqual([]);
  expect((await api.post(`/api/chunks/${id}/resume`, { data: { by: 'operator' } })).status).toBe(202);
  expect(await stepChunkUntil(runnerHost, api, id, (status) => status === 'done')).toBe('done');
  const leases = (await runner.leases()).body.items.filter((lease) => lease.chunk_id === id);
  expect(leases).toHaveLength(1);
  expect(leases[0]!.state).toBe('closed');
});

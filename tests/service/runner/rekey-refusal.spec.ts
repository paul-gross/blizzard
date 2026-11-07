import { expect, runnerTest as test } from '../../harness/fixtures';

test('rekey without a route is refused before a claim and succeeds after one', async ({ mockHub }) => {
  const seeded = await mockHub.seed.chunk({ entry: 'build', nodes: { build: { executor: 'runner', session: 'resume', judged_by: 'worker', prompt: 'work', judgement_prompt: 'judge', choices: [{ name: 'pass', to: 'done', description: 'done' }] } } });
  expect(seeded.status).toBe(201);
  const id = seeded.body.chunk_id;
  const added = await mockHub.seed.runner({ name: 'rekey-refusal' });
  const runner = mockHub.fleet(added.body.token);
  const path = `/api/fleet/chunks/${id}/route-token`;
  const refused = await runner.post(path);
  expect(refused.status).toBe(404);
  expect(refused.headers['content-type']).toContain('application/problem+json');
  expect(refused.body).toMatchObject({ type: 'urn:blizzard:problem:route-not-found', status: 404, instance: path, detail: `chunk ${id} has no live route` });
  expect((await runner.get(`/api/fleet/chunks/${id}`)).body.status).toBe('ready');

  expect((await runner.post('/api/fleet/runners', { data: { workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] } })).status).toBe(201);
  expect((await runner.post('/api/fleet/routes', { data: { chunk_id: id, workspace_id: 'w1', environment_ids: [] } })).status).toBe(201);
  expect((await runner.post(path)).status).toBe(200);
});

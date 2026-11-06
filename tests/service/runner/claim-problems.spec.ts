import { expect, runnerTest as test } from '../../harness/fixtures';

test('mock hub preserves the same claim problem and legacy fields', async ({ mockHub }) => {
  const seeded = await mockHub.seed.chunk({ entry: 'build', nodes: { build: { executor: 'runner', session: 'resume', judged_by: 'worker', prompt: 'work', judgement_prompt: 'judge', choices: [{ name: 'pass', to: 'done', description: 'done' }] } }, status: 'not_ready' });
  expect(seeded.status).toBe(201);
  const id = seeded.body.chunk_id;
  const added = await mockHub.seed.runner({ name: 'claim-problems' });
  const runner = mockHub.fleet(added.body.token);
  const claim = () => runner.post('/api/fleet/routes', { data: { chunk_id: id, workspace_id: 'w1', environment_ids: [] } });

  const unregistered = await claim();
  expect(unregistered.status).toBe(403);
  expect(unregistered.headers['content-type']).toContain('application/problem+json');
  expect(unregistered.body).toMatchObject({ type: 'urn:blizzard:problem:claim-runner-unregistered', instance: '/api/fleet/routes', status: 403, chunk_id: id, runner_id: added.body.runner_id });

  expect((await runner.post('/api/fleet/runners', { data: { workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] } })).status).toBe(201);
  const notReady = await claim();
  expect(notReady.status).toBe(409);
  expect(notReady.headers['content-type']).toContain('application/problem+json');
  expect(notReady.body).toMatchObject({ type: 'urn:blizzard:problem:claim-not-ready', instance: '/api/fleet/routes', status: 'not_ready', chunk_id: id });
  expect((await runner.get(`/api/fleet/chunks/${id}`)).body.status).toBe('not_ready');
});

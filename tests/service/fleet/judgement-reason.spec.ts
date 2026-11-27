import { expect, stepUntil, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('a real runner judgement reason survives the real hub and is read in chunk history', async ({ api, issue, runnerHosts }) => {
  test.slow();
  const reason = 'The real runner judged the change ready.';
  const graph = {
    name: 'default-delivery', entry: 'build', nodes: {
      build: {
        executor: 'runner', prompt: "print('worked')\n",
        judgement: { prompt: `verdict('pass', '${reason}')\n`, choices: { pass: { description: 'ready', to: 'deliver' } } },
        retries: { max: 1, exhausted: 'escalate' },
      },
      deliver: {
        executor: 'hub', action: 'pass',
        judgement: { choices: { success: { description: 'delivered', to: 'done' }, failure: { description: 'failed', to: 'build' } } },
      },
    },
  };
  const created = await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(graph) } });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  const host = await runnerHosts.create();
  const { chunk } = await issue('real judgement reason');
  const id = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  const detail = await stepUntil(host, async () => (await api.get(`/api/chunks/${id}`)).body,
    (current) => current.history.some((entry: { choice_name: string }) => entry.choice_name === 'pass'));
  expect(detail.history).toEqual(expect.arrayContaining([
    expect.objectContaining({ choice_name: 'pass', reason }),
  ]));
});

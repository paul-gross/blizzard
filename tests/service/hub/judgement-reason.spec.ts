import { expect, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('a mock runner completion exposes its judgement reason in chunk history', async ({ api, issue, mockRunner }) => {
  const reason = 'The mock runner found the change ready.';
  const graph = {
    name: 'default-delivery', entry: 'build', nodes: {
      build: {
        executor: 'runner', prompt: '# build',
        judgement: { prompt: '# judge', choices: { pass: { description: 'ready', to: 'review' } } },
        retries: { max: 1, exhausted: 'escalate' },
      },
      review: {
        executor: 'runner', prompt: '# review',
        judgement: { prompt: '# judge', choices: { pass: { description: 'reviewed', to: 'done' } } },
        retries: { max: 1, exhausted: 'escalate' },
      },
    },
  };
  const created = await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(graph) } });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  const { chunk } = await issue('judgement reason');
  const id = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  const runner = await mockRunner('reason-reporter');
  expect((await runner.drive('register')).body.status).toBe(201);
  expect((await runner.drive('claim', { chunk_id: id })).body.claimed).toBe(true);
  const completed = await runner.drive('complete', { chunk_id: id, choice: 'pass', reason });
  expect(completed.body.drove).toBe(true);
  expect(completed.body.response?.outcome).toBe('next');

  const detail = await api.get(`/api/chunks/${id}`);
  expect(detail.status).toBe(200);
  expect(detail.body.history).toEqual(expect.arrayContaining([
    expect.objectContaining({ choice_name: 'pass', reason }),
  ]));
});

import { expect, test } from '../../harness/fixtures';

// A runner reaches urgent work first: the matched peek walks claim order (urgency before rank), so an urgent chunk
// ranked last is the one the runner is offered and claims, while the queue read stays in rank order.
test.use({ isolation: 'fresh-tenant' });

test('test_a_runner_claims_the_urgent_chunk_ranked_last', async ({ api, issue, mockRunner }) => {
  const first = (await issue('ranked first')).chunk.chunk_id;
  const second = (await issue('ranked second')).chunk.chunk_id;
  const urgent = (await issue('urgent, ranked last')).chunk.chunk_id;
  for (const chunkId of [first, second, urgent]) {
    expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
  }
  expect((await api.post('/api/urgencies', { data: { slug: 'urgent', after: null } })).status).toBe(201);
  expect((await api.patch(`/api/chunks/${urgent}`, { data: { urgency: 'urgent' } })).status).toBe(202);

  const queue = await api.get('/api/queue');
  expect(queue.body.entries.map((entry: { chunk_id: string }) => entry.chunk_id)).toEqual([first, second, urgent]);

  const runner = await mockRunner('runner-claim-order');
  expect((await runner.drive('register')).body.status).toBe(201);
  const peek = await runner.drive('peek');
  expect(peek.body.response.entries[0].chunk_id).toBe(urgent);

  expect((await runner.drive('claim', { chunk_id: urgent })).body.claimed).toBe(true);
  expect((await api.get(`/api/chunks/${urgent}`)).body.status).toBe('running');
});

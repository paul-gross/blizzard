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
  expect(peek.body.response.entries.at(0)?.chunk_id).toBe(urgent);

  expect((await runner.drive('claim', { chunk_id: urgent })).body.claimed).toBe(true);
  expect((await api.get(`/api/chunks/${urgent}`)).body.status).toBe('running');
});

// The hub explains a runner's next claim from what it holds of the runner: an urgent dependent is passed as blocked by
// its prerequisite, until raising the prerequisite's urgency makes it the runner's next claim.
test('test_the_next_claim_explanation_passes_a_blocked_urgent_dependent_until_its_prerequisite_is_raised', async ({
  api,
  issue,
  mockRunner,
}) => {
  const filler = (await issue('ranked first')).chunk.chunk_id;
  const prerequisite = (await issue('the prerequisite')).chunk.chunk_id;
  const dependent = (await issue('the urgent dependent')).chunk.chunk_id;
  for (const chunkId of [filler, prerequisite, dependent]) {
    expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
  }
  expect((await api.post('/api/urgencies', { data: { slug: 'urgent', after: null } })).status).toBe(201);
  expect(
    (await api.post(`/api/chunks/${dependent}/dependencies`, { data: { prerequisite_chunk_id: prerequisite } })).status,
  ).toBe(202);
  expect((await api.patch(`/api/chunks/${dependent}`, { data: { urgency: 'urgent' } })).status).toBe(202);

  const runner = await mockRunner('runner-next-claim');
  expect((await runner.drive('register')).body.status).toBe(201);

  const before = await api.get(`/api/runners/${runner.runnerId}/next-claim`);
  expect(before.status).toBe(200);
  expect(before.body.next?.chunk_id).toBe(filler);
  expect(
    before.body.passed.map((entry: { chunk_id: string; reason: string; prerequisite: string | null }) => [
      entry.chunk_id,
      entry.reason,
      entry.prerequisite,
    ]),
  ).toEqual([[dependent, 'blocked', prerequisite]]);
  expect(before.body.passed.at(0)?.urgency?.slug).toBe('urgent');

  expect((await api.patch(`/api/chunks/${prerequisite}`, { data: { urgency: 'urgent' } })).status).toBe(202);
  const after = await api.get(`/api/runners/${runner.runnerId}/next-claim`);
  expect(after.status).toBe(200);
  expect(after.body.next?.chunk_id).toBe(prerequisite);
  expect(after.body.passed).toEqual([]);
});

import { expect, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('deleting a chunk withdraws its item and publishes the deletion', async ({ api, events }) => {
  const created = await api.post('/api/work-sources/hub/items', { data: { title: 'Delete item', body: 'body' } });
  expect(created.status).toBe(201);
  const { ref, chunk_id: chunkId } = created.body;
  const stream = await events.open({ barrier: true });

  const deleted = await api.delete(`/api/chunks/${chunkId}`, { data: { by: 'alice' } });
  expect(deleted.status).toBe(202);
  expect(deleted.body.chunk_id).toBe(chunkId);
  expect((await api.get(`/api/chunks/${chunkId}`)).status).toBe(404);
  expect((await api.get(`/api/work-sources/hub/items/${ref}`)).body.closure).toBe('withdrawn');
  const frames = await stream.barrier();
  expect(frames.filter((frame) => frame.event === 'chunk-changed' && frame.data.chunk_id === chunkId)).toEqual([
    expect.objectContaining({ data: expect.objectContaining({ chunk_id: chunkId, cause: 'deleted', by: 'alice', status: 'not_ready' }) }),
  ]);
  expect(frames.some((frame) => frame.event === 'queue-changed')).toBe(true);
  expect((await api.delete(`/api/chunks/${chunkId}`, { data: {} })).status).toBe(404);
});

test('withdrawing an item deletes its unacquired holder', async ({ api, events }) => {
  const created = await api.post('/api/work-sources/hub/items', { data: { title: 'Withdraw item', body: 'body' } });
  expect(created.status).toBe(201);
  const { ref, chunk_id: chunkId } = created.body;
  const stream = await events.open({ barrier: true });
  const withdrawn = await api.delete(`/api/work-sources/hub/items/${ref}`);
  expect(withdrawn.status).toBe(200);
  expect(withdrawn.body.closure).toBe('withdrawn');
  expect((await api.get(`/api/chunks/${chunkId}`)).status).toBe(404);
  expect((await api.get(`/api/work-sources/hub/items/${ref}`)).body.closure).toBe('withdrawn');
  const frames = await stream.barrier();
  expect(frames.some((frame) => frame.event === 'chunk-changed' && frame.data.chunk_id === chunkId && frame.data.cause === 'deleted')).toBe(true);
  expect(frames.some((frame) => frame.event === 'queue-changed')).toBe(true);
  expect((await api.delete(`/api/work-sources/hub/items/${ref}`)).status).toBe(409);
});

test('a standing dependent refuses both deletion and withdrawal without changing either item', async ({ api }) => {
  const first = await api.post('/api/work-sources/hub/items', { data: { title: 'Prerequisite', body: 'body' } });
  const second = await api.post('/api/work-sources/hub/items', { data: { title: 'Dependent', body: 'body' } });
  expect(first.status).toBe(201);
  expect(second.status).toBe(201);
  const prerequisite = first.body.chunk_id;
  const dependent = second.body.chunk_id;
  expect((await api.post(`/api/chunks/${dependent}/dependencies`, { data: { prerequisite_chunk_id: prerequisite } })).status).toBe(202);
  const refused = await api.delete(`/api/chunks/${prerequisite}`, { data: {} });
  expect(refused.status).toBe(409);
  expect(refused.body.detail).toContain(dependent);
  expect((await api.delete(`/api/work-sources/hub/items/${first.body.ref}`)).status).toBe(409);
  expect((await api.get(`/api/chunks/${prerequisite}`)).status).toBe(200);
  expect((await api.get(`/api/chunks/${dependent}`)).status).toBe(200);
  expect((await api.get(`/api/work-sources/hub/items/${first.body.ref}`)).body.closure).toBeNull();
  expect((await api.get(`/api/work-sources/hub/items/${second.body.ref}`)).body.closure).toBeNull();
  expect((await api.delete(`/api/chunks/${dependent}`, { data: {} })).status).toBe(202);
  expect((await api.delete(`/api/chunks/${prerequisite}`, { data: {} })).status).toBe(202);
});

test('an acquired holder refuses deletion and withdrawal', async ({ api, mockRunner }) => {
  const created = await api.post('/api/work-sources/hub/items', { data: { title: 'Acquired item', body: 'body' } });
  expect(created.status).toBe(201);
  const { ref, chunk_id: chunkId } = created.body;
  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
  const runner = await mockRunner('delete-refusal');
  expect((await runner.drive('register')).body.status).toBe(201);
  expect((await runner.drive('claim', { chunk_id: chunkId })).body.claimed).toBe(true);
  expect((await api.get(`/api/chunks/${chunkId}`)).body.status).toBe('running');
  const deleted = await api.delete(`/api/chunks/${chunkId}`, { data: {} });
  expect(deleted.status).toBe(409);
  expect(deleted.body.detail).toContain('running');
  expect((await api.delete(`/api/work-sources/hub/items/${ref}`)).status).toBe(409);
  expect((await api.get(`/api/chunks/${chunkId}`)).body.status).toBe('running');
  expect((await api.get(`/api/work-sources/hub/items/${ref}`)).body.closure).toBeNull();
});

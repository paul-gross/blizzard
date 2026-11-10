import { expect, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('editing a ready chunk\'s urgency names it on the chunk\'s view and publishes the queue\'s change', async ({ api, events }) => {
  const urgent = await api.post('/api/urgencies', { data: { slug: 'urgent' } });
  expect(urgent.status).toBe(201);
  const created = await api.post('/api/work-sources/hub/items', { data: { title: 'Urgent item', body: 'body' } });
  expect(created.status).toBe(201);
  const chunkId = created.body.chunk_id;
  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
  const before = await api.get('/api/queue');
  const position = before.body.entries.find((entry: { chunk_id: string }) => entry.chunk_id === chunkId).position;
  const stream = await events.open({ barrier: true });

  const edited = await api.patch(`/api/chunks/${chunkId}`, { data: { urgency: 'urgent' } });

  expect(edited.status).toBe(202);
  const named = { urgency_id: urgent.body.urgency_id, slug: 'urgent' };
  expect(edited.body.urgency).toEqual(named);
  const detail = await api.get(`/api/chunks/${chunkId}`);
  expect(detail.body.status).toBe('ready');
  expect(detail.body.urgency).toEqual(named);
  const queue = await api.get('/api/queue');
  expect(queue.body.entries).toContainEqual(expect.objectContaining({ chunk_id: chunkId, position, urgency: named }));
  const frames = await stream.barrier();
  expect(frames.some((frame) => frame.event === 'chunk-changed' && frame.data.chunk_id === chunkId && frame.data.cause === 'edited')).toBe(true);
  expect(frames.some((frame) => frame.event === 'queue-changed')).toBe(true);
});

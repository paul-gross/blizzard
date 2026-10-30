import { expect, stepUntil, test } from '../../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('test_a_verdict_less_exit_surfaces_a_critical_worker_lost_event', async ({ api, events, issue, runnerHost }) => {
  test.slow();
  const graph = JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: {
      executor: 'runner', prompt: 'pass\n', judgement: {
        prompt: 'pass\n', choices: { pass: { description: 'Green.', to: 'deliver' } },
      }, retries: { max: 1, exhausted: 'escalate' },
    },
    deliver: { executor: 'hub', action: 'test.answer', with: { choice: 'landed' }, judgement: { choices: {
      landed: { description: 'Landed.', to: 'done' },
      conflict: { description: 'Conflict.', to: 'build' },
      failure: { description: 'Failed.', to: 'build' },
    } } },
  } });
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph } })).status).toBe(201);
  const { chunk } = await issue('event-log');
  const chunkId = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);

  const subscription = await events.open({ barrier: true });
  const detail = await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunkId}`)).body,
    (body) => body.status === 'needs_human', 80);
  expect(detail.status).toBe('needs_human');
  const attempts = (await runnerHost.runners['runner-local']!.leasesForChunk(chunkId)).body.items
    .filter((lease) => lease.node_name === 'build')
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  expect(attempts).toHaveLength(2);
  expect(attempts.map((lease) => lease.state)).toEqual(['closed', 'closed']);

  const frames = await subscription.barrier();
  const lostFrames = frames.filter((frame) => frame.event === 'event-logged' && frame.data?.chunk_id === chunkId && frame.data?.kind === 'worker-lost');
  expect(lostFrames).toHaveLength(1);
  const response = await api.get('/api/events');
  expect(response.status).toBe(200);
  const lost = response.body.events.filter((event: { kind: string; chunk_id: string }) => event.kind === 'worker-lost' && event.chunk_id === chunkId);
  expect(lost).toHaveLength(1);
  expect(lost[0]).toMatchObject({ severity: 'critical', chunk_id: chunkId, detail: { via: expect.stringMatching(/^(advance|reap)$/) } });
});

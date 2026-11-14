import { expect, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('restarting an unclaimed chunk onto a gate records the move and opens its decision', async ({ api, issue }) => {
  const graph = JSON.stringify({
    name: 'default-delivery',
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        prompt: 'Build it.',
        judgement: { prompt: 'Assess it.', choices: { pass: { description: 'Built.', to: 'signoff' } } },
      },
      signoff: {
        executor: 'runner',
        judgement: { by: 'human', choices: { approve: { description: 'Approved.', to: 'done' }, reject: { description: 'Rejected.', to: 'build' } } },
      },
    },
  });
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph } })).status).toBe(201);
  const { chunk } = await issue('restart onto a gate');
  const id = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/restart`, { data: { node: 'signoff', by: 'operator' } })).status).toBe(202);
  const detail = await api.get(`/api/chunks/${id}`);
  expect(detail.status).toBe(200);
  expect(detail.body.status).toBe('waiting_on_human');
  expect(detail.body.current_node_name).toBe('signoff');
  expect(detail.body.decision.epoch).toBe(detail.body.latest_epoch);
  expect(detail.body.restarts).toHaveLength(1);
  expect(detail.body.restarts[0]).toMatchObject({ to_node_name: 'signoff', restarted_by: 'operator' });
  expect((await api.get('/api/decisions')).body.decisions).toHaveLength(1);
});

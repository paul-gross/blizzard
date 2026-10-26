import { expect, test } from '../../../harness/fixtures';

// The route-token check, enforced, driven by the mock runner's stale and omit levers against the real hub. Each test
// posts a graph and files its own runner, so each owns its tenant.
test.use({ isolation: 'fresh-tenant' });

const GRAPH = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: '# build',
      judgement: { prompt: '# judge', choices: { pass: { description: 'green', to: 'review' } } },
      retries: { max: 1, exhausted: 'escalate' },
    },
    review: {
      executor: 'runner',
      prompt: '# review',
      session: 'fresh',
      judgement: { prompt: '# judge', choices: { pass: { description: 'clean', to: 'deliver' } } },
      retries: { max: 1, exhausted: 'escalate' },
    },
    deliver: {
      executor: 'hub',
      action: 'pass',
      judgement: {
        choices: { success: { description: 'Delivered.', to: 'done' }, failure: { description: 'Failed to deliver.', to: 'build' } },
      },
    },
  },
});

test('test_route_token_present_by_default_is_accepted_under_enforce', async ({ api, issue, mockRunner }) => {
  expect((await api.post('/api/graphs', { data: { definition_yaml: GRAPH } })).status).toBe(201);
  const { chunk } = await issue('route token present');
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const runner = await mockRunner('runner-route-present');
  await runner.drive('register');
  const claim = await runner.drive('claim', { chunk_id: chunk.chunk_id });
  expect(claim.body.claimed).toBe(true);

  // No lever armed: the mock runner presents the claim's own token by default.
  const out = await runner.drive('complete', { chunk_id: chunk.chunk_id, choice: 'pass' });
  expect(out.body.response?.outcome, JSON.stringify(out.body)).toBe('next');
});

for (const [title, lever] of [
  ['test_route_token_stale_is_rejected_under_enforce_over_the_wire', 'stale_route_token'],
  ['test_route_token_omitted_is_rejected_under_enforce_over_the_wire', 'omit_route_token'],
] as const) {
  test(title, async ({ api, issue, mockRunner }) => {
    expect((await api.post('/api/graphs', { data: { definition_yaml: GRAPH } })).status).toBe(201);
    const { chunk } = await issue(title);
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const runner = await mockRunner('runner-route-refused');
    await runner.drive('register');
    expect((await runner.drive('claim', { chunk_id: chunk.chunk_id })).body.claimed).toBe(true);
    const before = (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.current_node_id;

    expect((await runner.lever(lever, { chunk_id: chunk.chunk_id })).status).toBe(200);
    const out = await runner.drive('complete', { chunk_id: chunk.chunk_id, choice: 'pass' });

    expect(out.body.response?.outcome, JSON.stringify(out.body)).toBe('failure');
    expect((await api.get(`/api/chunks/${chunk.chunk_id}`)).body.current_node_id).toBe(before);
  });
}

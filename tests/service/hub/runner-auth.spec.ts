import { expect, test } from '../../harness/fixtures';

// Revocation is keyed on the revoked hash: the same token that peeked fine before its revocation is refused after, and
// a call carrying no token is refused throughout. The test mints a runner, so it owns its tenant.
test.use({ isolation: 'fresh-tenant' });

test('test_a_running_hub_refuses_a_revoked_token_and_a_tokenless_call', async ({ api, fleet }) => {
  const runner = await fleet('r1');
  const registered = await runner.post('/api/fleet/runners', {
    data: { name: 'r1', workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] },
  });
  expect(registered.status).toBe(201);
  expect(registered.body.runner_id).toBe(runner.runnerId);
  expect((await runner.get('/api/fleet/queue/peek')).status).toBe(200);
  // `api` carries the tenant header and no `Authorization`.
  expect((await api.get('/api/fleet/queue/peek')).status).toBe(401);

  const revoked = await api.post(`/api/runners/${runner.runnerId}/token-revocations`, { data: { by: 'svc' } });
  expect(revoked.status).toBe(201);

  expect((await runner.get('/api/fleet/queue/peek')).status).toBe(401);
  expect((await api.get('/api/fleet/queue/peek')).status).toBe(401);
});

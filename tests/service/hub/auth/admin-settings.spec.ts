import { expect, test } from '../../../harness/fixtures';

// The settings store is global even when the caller has a tenant.
test.use({ isolation: 'fresh-hub', userRole: 'admin' });
const path = '/api/admin/settings';

test('hub administrator may read and write while tenant administrator is refused every route', async ({ api, user }) => {
  const me = await user.api.get('/api/me');
  expect(me.body.role).toBe('admin');
  expect(me.body.hub_admin).toBe(false);
  const revision = (await api.get(path)).body.revision as number;
  const calls = [
    (client: typeof api) => client.get(path),
    (client: typeof api) => client.get(path + '/changes'),
    (client: typeof api) => client.get(path + '/export'),
    (client: typeof api) => client.patch(path, { headers: { 'If-Match': String(revision) }, data: { set: { action_pass_seconds: 5 } } }),
    (client: typeof api) => client.post(path + '/apply', { data: { action_pass_seconds: 6 } }),
  ];
  for (const call of calls) {
    const denied = await call(user.api);
    expect(denied.status).toBe(403);
    expect(denied.body.type).toBe('urn:blizzard:problem:permission-required');
    const allowed = await call(api);
    expect(allowed.status, JSON.stringify(allowed.body)).toBe(200);
  }
});

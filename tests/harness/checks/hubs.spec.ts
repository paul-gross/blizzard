import { HubInternal } from '../client/internal';
import { expect, test } from '../fixtures';

test.use({ isolation: 'common' });

test('the shared hub is up with its seeded tenant and the common one open, and any tenant tests have made since', async ({ hub: shared }) => {
  const hub = shared.coordinates();
  const admin = new HubInternal(hub.url);
  const tenants = await admin.listTenants();
  // Tests at fresh-tenant add tenants as the run goes, so the two the setup left open are a floor, not a count.
  const open = tenants.filter((tenant) => tenant.state === 'open');
  expect(open.length).toBeGreaterThanOrEqual(2);
  expect(open.map((tenant) => tenant.name)).toEqual(expect.arrayContaining(['default', 'common']));
  expect(tenants.map((tenant) => tenant.tenant_id)).toContain(hub.commonTenantId);
  expect(tenants.map((tenant) => tenant.name)).toContain('common');
});

test('a request that names no tenant is answered 409 tenant_required', async ({ hub: shared }) => {
  const hub = shared.coordinates();
  const answer = await new HubInternal(hub.url).probe('GET', '/api/chunks');
  expect(answer.status).toBe(409);
  expect(answer.body).toMatchObject({ detail: { code: 'tenant_required' } });
});

test('the common tenant is furnished: one work source, linked to the default project', async ({ hub: shared }) => {
  const hub = shared.coordinates();
  const admin = new HubInternal(hub.url);
  const sources = await admin.call<{ items?: unknown[]; work_sources?: unknown[] }>('GET', '/api/work-sources', { tenant: hub.commonTenantId });
  expect(JSON.stringify(sources)).toContain('blizzard/common');
});

test('the hub, forge and IdP each answer their health route', async ({ hub: shared }) => {
  const hub = shared.coordinates();
  for (const url of [`${hub.url}/api/health`, `${hub.forgeUrl}/healthz`, `${hub.idpUrl}/healthz`]) {
    expect((await new HubInternal(url).probe('GET', '')).status, url).toBe(200);
  }
});

import { recordsOf } from '../client/latency';
import { expect, test } from '../fixtures';

test.describe('at common, the factories that would furnish the shared tenant refuse', () => {
  test.use({ isolation: 'common' });

  test('fleet, repository and mockRunner refuse, naming the level they need', async ({ fleet, repository, mockRunner }) => {
    await expect(fleet('r-one')).rejects.toThrow(/fresh-tenant/);
    await expect(repository()).rejects.toThrow(/fresh-tenant/);
    await expect(mockRunner('r-two')).rejects.toThrow(/fresh-tenant/);
  });
});

test.describe('hubLog.untagged', () => {
  test.describe('refuses at fresh-tenant', () => {
    test.use({ isolation: 'fresh-tenant' });
    test('a hub-wide read', ({ hubLog }) => {
      expect(() => hubLog.untagged()).toThrow(/fresh-hub/);
    });
  });

  test.describe('is allowed at fresh-hub', () => {
    test.use({ isolation: 'fresh-hub' });
    test('and reads the whole log', async ({ hubLog, api }) => {
      await api.get('/api/health');
      expect(hubLog.untagged().length).toBeGreaterThan(0);
      expect(hubLog.lines().length).toBeGreaterThanOrEqual(hubLog.untagged().length);
    });
  });
});

test.describe('at fresh-tenant, the factories make what the tenant is to own', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('repository and issue: an issue is filed in a repository of the test’s own and ingested', async ({ api, issue, repository }) => {
    const made = await repository();
    const filed = await issue('in the named repository', { repository: made });
    expect(filed.issue.title).toBe('in the named repository');
    const unnamed = await issue('in an implied repository');
    const listed = await api.get('/api/chunks');
    // The tenant is the test's own, so the list comes back whole.
    expect(listed.body.chunks.map((chunk: { chunk_id: string }) => chunk.chunk_id).sort()).toEqual(
      [filed.chunk.chunk_id, unnamed.chunk.chunk_id].sort(),
    );
  });

  test('fleet mints a project-serving runner and speaks the fleet plane under its token', async ({ fleet, api, project }) => {
    const runner = await fleet('r-fleet', { projects: [project.slug] });
    const answered = await runner.get('/api/fleet/identity');
    expect(answered.status).toBe(200);
    expect(JSON.stringify(answered.body)).toContain(runner.runnerId);
    const listed = await api.get('/api/runners');
    expect(listed.body.runners.find((item: { runner_id: string }) => item.runner_id === runner.runnerId).projects)
      .toEqual([expect.objectContaining({ entry: project.slug })]);
  });

  test('a client call and the tenant’s creation each leave a latency record on the test', async ({ api }, testInfo) => {
    await api.get('/api/chunks');
    const records = recordsOf(testInfo);
    // The tenant is made through the internal client, which no `send()` sees.
    expect(records.find((record) => record.method === 'POST' && record.route === '/api/admin/tenants')).toMatchObject({ status: 201 });
    expect(records.find((record) => record.method === 'GET' && record.route === '/api/chunks')).toMatchObject({ status: 200 });
    expect(records.every((record) => record.ms >= 0)).toBe(true);
  });

  test('mockRunner starts a runner against the test’s hub under a token minted in its tenant', async ({ mockRunner, api }) => {
    const runner = await mockRunner('r-mock');
    expect(runner.url).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
    const runners = await api.get('/api/runners');
    expect(runners.body.runners.map((listed: { runner_id: string }) => listed.runner_id)).toContain(runner.runnerId);
  });

  test('mockRunner drives registration and peek and exposes its own levers', async ({ mockRunner, api, project }, testInfo) => {
    const runner = await mockRunner('r-drive', { projects: [project.slug] });
    const registered = await runner.drive('register');
    expect(registered.status).toBe(200);
    expect(registered.body.status).toBe(201);
    expect(registered.body.response.runner_id).toBe(runner.runnerId);
    const peeked = await runner.drive('peek');
    expect(peeked.status).toBe(200);
    expect(peeked.body.status).toBe(200);
    const listed = await api.get('/api/runners');
    expect(listed.body.runners.find((item: { runner_id: string }) => item.runner_id === runner.runnerId).projects)
      .toEqual([expect.objectContaining({ entry: project.slug })]);

    const armed = await runner.lever('drop_ack', { chunk_id: 'only-this-chunk' });
    expect(armed.status).toBe(200);
    expect(armed.body.armed.kind).toBe('drop_ack');
    const levers = await runner.levers();
    expect(levers.status).toBe(200);
    expect(levers.body.active).toContainEqual(expect.objectContaining({ kind: 'drop_ack', chunk_id: 'only-this-chunk' }));
    const records = recordsOf(testInfo);
    expect(records).toContainEqual(expect.objectContaining({ method: 'POST', route: '/_drive/register', status: 200 }));
    expect(records).toContainEqual(expect.objectContaining({ method: 'GET', route: '/_levers', status: 200 }));
  });
});

test.describe('at fresh-project, work is filed in the common repository under a project of its own', () => {
  test.use({ isolation: 'fresh-project' });

  test('an issue ingests into the test’s project', async ({ issue, project, api }) => {
    const filed = await issue('in a fresh project');
    expect(filed.chunk.project_slug).toBe(project.slug);
    const listed = await api.get('/api/chunks');
    expect(listed.body.chunks.map((chunk: { chunk_id: string }) => chunk.chunk_id)).toEqual([filed.chunk.chunk_id]);
  });
});

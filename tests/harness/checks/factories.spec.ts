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

  test('fleet mints a runner and speaks the fleet plane under its token', async ({ fleet }) => {
    const runner = await fleet('r-fleet');
    const answered = await runner.get('/api/fleet/identity');
    expect(answered.status).toBe(200);
    expect(JSON.stringify(answered.body)).toContain(runner.runnerId);
  });

  test('mockRunner starts a runner against the test’s hub under a token minted in its tenant', async ({ mockRunner, api }) => {
    const runner = await mockRunner('r-mock');
    expect(runner.url).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
    const runners = await api.get('/api/runners');
    expect(runners.body.runners.map((listed: { runner_id: string }) => listed.runner_id)).toContain(runner.runnerId);
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

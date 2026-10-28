import { expect, test } from '../fixtures';

test.describe('at fresh-tenant', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('the host enrolls only in the test’s tenant, its stream connects, and cli reaches it', async ({ runnerHost, runnerStream, api }) => {
    const runner = runnerHost.runners['runner-local'];
    const listed = await api.get('/api/runners');
    expect((listed.body.runners as { runner_id: string }[]).map((entry) => entry.runner_id)).toEqual([runner?.id]);
    const stream = await runnerStream(runnerHost.url);
    expect(stream.contentType).toMatch(/^text\/event-stream/);
    const result = await runnerHost.cli(['tick']);
    expect(result.code, result.stdout + result.stderr).toBe(0);
  });

  test('a host client reaches the host’s local API', async ({ runnerHost, runnerHostClient }) => {
    const client = runnerHostClient(runnerHost.url, { logContext: runnerHost.logTail });
    const listed = await client.runners();
    expect(listed.status).toBe(200);
  });
});

test.describe('at fresh-hub', () => {
  test.use({ isolation: 'fresh-hub' });

  test('the host enrolls in the fresh hub’s seeded tenant', async ({ runnerHost, api, tenant }) => {
    const listed = await api.get('/api/runners');
    expect((listed.body.runners as { runner_id: string }[]).map((entry) => entry.runner_id)).toContain(runnerHost.runners['runner-local']?.id);
    expect(tenant.id).toBeTruthy();
    expect((await runnerHost.step()).outcome).toBe('completed');
  });
});

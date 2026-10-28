import { expect, test } from '../fixtures';

test.describe('at fresh-tenant', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('the host enrolls in the test’s tenant through init --tenant, and steps', async ({ runnerHost, api, ledger, tenant }) => {
    const runner = runnerHost.runners['runner-local'];
    expect(runner).toBeDefined();
    const listed = await api.get('/api/runners');
    expect(listed.status).toBe(200);
    const ids = (listed.body.runners as { runner_id: string }[]).map((entry) => entry.runner_id);
    expect(ids).toContain(runner?.id);
    expect((await runnerHost.step()).outcome).toBe('completed');
    expect((await runner?.status())?.body.runner_id).toBe(runner?.id);
    expect(ledger.has('runner', runner?.id)).toBe(true);
    expect(ledger.ids('host')).toHaveLength(1);
    expect(tenant.id).toBeTruthy();
  });

  test('a second host in the same tenant names its default runner apart and enrolls beside the first', async ({ runnerHost, runnerHosts, api }) => {
    const second = await runnerHosts.create();
    expect(Object.keys(second.runners)).toEqual(['runner-local-1']);
    const ids = ((await api.get('/api/runners')).body.runners as { runner_id: string }[]).map((entry) => entry.runner_id);
    expect(ids).toEqual(expect.arrayContaining([runnerHost.runners['runner-local']?.id, second.runners['runner-local-1']?.id]));
    expect((await second.step()).outcome).toBe('completed');
  });
});

test.describe('below fresh-tenant', () => {
  test.use({ isolation: 'common' });

  test('the fixture refuses, naming the level it needs', async ({ runnerHosts }) => {
    await expect(runnerHosts.create()).rejects.toThrow(/fresh-tenant/);
  });
});

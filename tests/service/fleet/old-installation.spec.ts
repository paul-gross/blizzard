import { expect, oldInstallationTest as test } from '../../harness/fixtures';

test.setTimeout(300_000);

test('a previous-release runner registers and claims work after the hub upgrade and a live settings write', async ({ oldInstallation }) => {
  const { api, runnerId } = oldInstallation;
  const runners = await api.get('/api/runners');
  expect((runners.body.runners as { runner_id: string }[]).map((runner: { runner_id: string }) => runner.runner_id)).toContain(runnerId);
  const settings = (await api.get('/api/admin/settings')).body;
  const edited = await api.patch('/api/admin/settings', {
    headers: { 'If-Match': String(settings.revision) }, data: { set: { action_pass_seconds: 5 } },
  });
  expect(edited.status, JSON.stringify(edited.body)).toBe(200);
  const current = await api.get('/api/admin/settings');
  expect(current.body.revision).toBe((settings.revision as number) + 1);
  expect(current.body.settings).toEqual(expect.arrayContaining([expect.objectContaining({ key: 'action_pass_seconds', value: 5 })]));
  const chunk = await oldInstallation.ingest('old runner claims after settings write');
  await expect.poll(async () => {
    const response = await api.get(`/api/chunks/${chunk}`);
    const usage = response.body.usage as { runner_id: string | null }[];
    return usage.some((entry) => entry.runner_id === runnerId);
  }, { timeout: 120_000 }).toBe(true);
});

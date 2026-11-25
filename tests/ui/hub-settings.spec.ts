import { expect, test } from '../harness/fixtures';

test.use({ isolation: 'fresh-hub' });

test('hub operator edits a sweep interval', async ({ page, api, wideViewport }) => {
  await page.setViewportSize(wideViewport);
  await page.goto('/admin/hub-settings');
  await expect(page.getByTestId('admin-tab-hub-settings')).toBeVisible();
  const before = await api.get('/api/admin/settings');
  const source = before.body.settings.find((row: { key: string }) => row.key === 'egress.sweep_seconds').source;
  await page.getByRole('button', { name: 'Edit egress.sweep_seconds' }).click();
  // A key at its default opens with Default chosen and the value disabled.
  if (source === 'default') await page.getByTestId('hub-setting-edit-field-useDefault').getByRole('button').click();
  await page.getByRole('textbox', { name: 'Value' }).fill('75');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Edit egress.sweep_seconds' })).toHaveCount(0);
  const read = await api.get('/api/admin/settings');
  expect(read.body.settings.find((row: { key: string }) => row.key === 'egress.sweep_seconds').value).toBe(75);
  await expect(page.getByRole('heading', { name: `Revision ${read.body.revision} · edit` })).toBeVisible();
});

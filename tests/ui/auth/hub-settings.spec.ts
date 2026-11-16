import { expect, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-hub', userRole: 'admin' });

test('a tenant administrator cannot find hub settings', async ({ page, user }) => {
  const identity = await user.api.get('/api/me');
  expect(identity.body.role).toBe('admin');
  expect(identity.body.hub_admin).toBe(false);
  const state = await user.context.storageState();
  await page.context().addCookies(state.cookies);
  await page.goto('/admin/members');
  await expect(page.getByTestId('admin-subnav')).toBeVisible();
  await expect(page.getByTestId('admin-tab-hub-settings')).toHaveCount(0);
  await page.goto('/admin/hub-settings');
  await expect(page.getByText('Hub settings are not available.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Hub settings', exact: true })).toHaveCount(0);
});

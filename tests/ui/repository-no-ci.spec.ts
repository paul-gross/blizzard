import { expect, test } from '../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('repository no-CI setting is editable on desktop and readable on a phone', async ({ repository, api, page, wideViewport, narrowViewport }) => {
  const repo = await repository();
  await page.setViewportSize(wideViewport);
  await page.goto(`/admin/repositories/${repo.name}`);
  const facts = page.getByTestId('admin-repository-facts');
  await expect(facts).toContainText('Allow no CI');
  await expect(facts).toContainText('No');
  await expect(facts).toContainText('not failing or pending checks');
  await expect(facts).toContainText('first turn of a visit');

  await page.getByTestId('admin-repository-edit').click();
  const checkbox = page.getByTestId('admin-repository-edit-field-allow_no_ci');
  await expect(checkbox).not.toBeChecked();
  await expect(page.getByTestId('admin-repository-edit-hint')).toContainText('absent checks');
  await checkbox.check();
  await page.getByTestId('admin-repository-edit-submit').click();
  await expect(facts).toContainText('Yes');
  expect((await api.get(`/api/repositories/${repo.name}`)).body.allow_no_ci).toBe(true);
  await page.reload();
  await expect(facts).toContainText('Yes');

  await page.setViewportSize(narrowViewport);
  await expect(facts).toContainText('Yes');
  await expect(facts).toContainText('first turn of a visit');
  await expect(page.getByTestId('admin-repository-edit')).toHaveCount(0);
  await expect(page.getByTestId('admin-repository-cli')).toContainText(`blizzard hub repo edit ${repo.name}`);
});

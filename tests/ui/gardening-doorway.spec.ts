import { expect, test } from '../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('mobile gardening project cards select a lens and browser Back returns to the doorway', async ({ api, page }) => {
  expect((await api.post('/api/projects', { data: { slug: 'garden-one', name: 'Garden One', description: '' } })).status).toBe(201);
  expect((await api.post('/api/projects', { data: { slug: 'garden-two', name: 'Garden Two', description: '' } })).status).toBe(201);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/gardening');
  const card = page.getByTestId('gardening-doorway-card-garden-one');
  await expect(card).toContainText('Garden One');
  await expect(card).toContainText('0 pending proposals');
  await expect(card).not.toContainText('0 cadences');
  await expect(card).not.toContainText('0 routines');
  await card.click();
  await expect(page).toHaveURL(/\/p\/garden-one\/gardening\/proposals$/);
  await expect(page.getByTestId('gardening-subnav')).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/gardening$/);
  await expect(card).toBeVisible();
});

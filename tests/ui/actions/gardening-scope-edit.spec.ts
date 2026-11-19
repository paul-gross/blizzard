import { expect, test } from '../../harness/fixtures';

// The gardening scope panel's inline description edit, on a form: a blank description is refused at the field before
// anything is sent and focuses it, and a valid one is saved through the hub and read back on the panel. It mints a
// scope, so the test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

test('test_gardening_scope_edit_browser', async ({ api, page, wideViewport }, testInfo) => {
  const slug = `scope-${testInfo.testId.replace(/[^a-z0-9]/gi, '').slice(0, 8).toLowerCase()}`;
  expect((await api.post('/api/scopes', { data: { slug, description: 'the original beds' } })).status).toBe(201);

  await page.setViewportSize(wideViewport);
  await page.goto('/gardening/scopes');
  await page.getByTestId(`gardening-scope-row-${slug}`).click();
  const description = page.getByTestId('gardening-scope-panel-description');
  await expect(description).toContainText('the original beds');

  await page.getByTestId('gardening-scope-panel-edit').click();
  const input = page.getByTestId('gardening-scope-panel-description-input');
  await expect(input).toHaveValue('the original beds');

  // A blank description is refused at the field, sends nothing, keeps the editor open and focuses the input.
  await input.fill('   ');
  await page.getByTestId('gardening-scope-panel-description-submit').click();
  await expect(page.locator('.edit-field')).toContainText('Enter a description');
  await expect(input).toBeFocused();
  expect((await api.get('/api/scopes')).body.find((s: { slug: string }) => s.slug === slug).description).toBe(
    'the original beds',
  );

  // A valid description saves, the editor closes, and the hub holds the trimmed text.
  await input.fill('  the replanted beds  ');
  await page.getByTestId('gardening-scope-panel-description-submit').click();
  await expect(input).toHaveCount(0);
  await expect(description).toContainText('the replanted beds');
  expect((await api.get('/api/scopes')).body.find((s: { slug: string }) => s.slug === slug).description).toBe(
    'the replanted beds',
  );

  // Cancel drops an unsaved draft: reopening finds the saved text.
  await page.getByTestId('gardening-scope-panel-edit').click();
  await input.fill('half-typed');
  await page.getByTestId('gardening-scope-panel-description-cancel').click();
  await expect(input).toHaveCount(0);
  await page.getByTestId('gardening-scope-panel-edit').click();
  await expect(input).toHaveValue('the replanted beds');
});

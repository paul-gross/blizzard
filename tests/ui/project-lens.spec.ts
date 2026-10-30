import { expect, test } from '../harness/fixtures';

// The board's project lens in a real router against the hub's own project resolution: a lensed deep link opens under
// its lens, a link naming a project by a slug it has left or by its id is rewritten in place to the current slug, and a
// link naming no project of the tenant opens the same view on All, with a notice. It mints and moves a project, so the
// test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

test('test_project_lens_deep_links_resolve_in_the_browser', async ({ api, page, wideViewport }, testInfo) => {
  const suffix = testInfo.testId.replace(/[^a-z0-9]/gi, '').slice(0, 8).toLowerCase();
  const formerSlug = `glacier-${suffix}`;
  const currentSlug = `ice-shelf-${suffix}`;
  const created = await api.post('/api/projects', { data: { slug: formerSlug, name: 'Glacier', description: '' } });
  expect(created.status).toBe(201);
  const project = created.body;
  const moved = await api.patch(`/api/projects/${project.project_id}`, {
    data: { slug: currentSlug },
    headers: { 'If-Match': String(project.revision) },
  });
  expect(moved.status).toBe(200);
  expect(moved.body.slug).toBe(currentSlug);

  await page.setViewportSize(wideViewport);

  // A deep link under the default project's lens opens the board there.
  await page.goto('/p/default/board');
  await expect(page.getByTestId('board-shell')).toBeVisible();
  await expect(page).toHaveURL(/\/p\/default\/board$/);
  await expect(page.getByTestId('project-lens-default')).toHaveAttribute('aria-pressed', 'true');

  // A slug the project has left is replaced by its current slug.
  await page.goto(`/p/${formerSlug}/board`);
  await expect(page).toHaveURL(new RegExp(`/p/${currentSlug}/board$`));
  await expect(page.getByTestId('board-shell')).toBeVisible();
  await expect(page.getByTestId(`project-lens-${currentSlug}`)).toHaveAttribute('aria-pressed', 'true');

  // So is the project's id.
  await page.goto(`/p/${project.project_id}/board`);
  await expect(page).toHaveURL(new RegExp(`/p/${currentSlug}/board$`));

  // A project the tenant does not hold opens the same view on All, with a notice.
  await page.goto('/p/no-such-project/board');
  await expect(page).toHaveURL(/\/board$/);
  await expect(page).not.toHaveURL(/\/p\//);
  await expect(page.getByTestId('lens-notice')).toBeVisible();
  await expect(page.getByTestId('project-lens-all')).toHaveAttribute('aria-pressed', 'true');
});

import { expect, stepUntil, test } from '../harness/fixtures';

// The board and admin operate on a tenant and runner host owned by this test, never a seeded or live fleet.
test.use({ isolation: 'fresh-tenant' });

test('urgency created and reordered in admin changes ready work and retirement moves it', async ({ api, issue, runnerHost, page, wideViewport }) => {
  test.setTimeout(180000);
  const runnerId = runnerHost.runners['runner-local']!.id;
  // Register before there is work, so stepping the host cannot claim either chunk.
  for (let pass = 0; pass < 2; pass++) expect((await runnerHost.step()).outcome).toBe('completed');
  const first = (await issue('ordinary work')).chunk.chunk_id;
  const urgent = (await issue('priority work')).chunk.chunk_id;
  for (const id of [first, urgent]) expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);

  await page.setViewportSize(wideViewport);
  await page.goto('/admin/urgencies');
  await page.getByTestId('admin-urgencies-new').click();
  await page.getByTestId('admin-urgencies-create-field-slug').fill('expedite');
  await page.getByTestId('admin-urgencies-create-field-name').fill('Expedite');
  await page.getByTestId('admin-urgencies-create-field-mark').fill('🔥');
  await page.getByTestId('admin-urgencies-create-submit').click();
  await expect(page.getByTestId('admin-urgency-detail')).toContainText('Expedite');
  const list = async () => (await api.get('/api/urgencies')).body.urgencies as { urgency_id: string; slug: string; default: boolean; emoji: string | null }[];
  const created = (await list()).find((u) => u.slug === 'expedite')!;
  const standard = (await list()).find((u) => u.default)!;
  expect(created.emoji).toBe('🔥');

  // Drag the actual handle above the default row, exercising CDK's pointer interaction and the position write.
  const rows = page.locator('.urgency-item');
  const source = rows.filter({ hasText: 'expedite' });
  const target = rows.filter({ hasText: standard.slug });
  const from = (await source.locator('.drag-handle').boundingBox())!;
  const to = (await target.boundingBox())!;
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2 - 12, { steps: 4 });
  await page.mouse.move(to.x + 10, to.y + 2, { steps: 25 });
  await page.mouse.move(to.x + 10, to.y + 1, { steps: 2 });
  await page.mouse.up();
  await expect.poll(async () => (await list()).map((u) => u.slug)).toEqual(['expedite', standard.slug]);

  await page.goto('/board');
  const ready = page.locator('[data-col="ready"]');
  const card = ready.locator(`[data-chunk="${urgent}"]`);
  await expect(card).toBeVisible();
  // Keep the runner's detail open across the board edit and retirement, to exercise live Next updates.
  const fleetPage = await page.context().newPage();
  await fleetPage.setViewportSize(wideViewport);
  await fleetPage.goto('/fleet');
  await fleetPage.locator('[data-testid^="fleet-card-hs_"]').first().click();
  const runner = fleetPage.locator(`[data-testid="fleet-runner"][data-runner="${runnerId}"]`);
  const nextLine = runner.getByTestId('runner-next-claim');
  await expect(nextLine.getByTestId('runner-next-link')).toHaveAttribute('title', first);
  await card.click();
  await page.getByTestId('chunk-urgency-menu').click();
  await page.getByRole('menuitemradio', { name: 'Expedite' }).click();
  await expect.poll(async () => (await api.get(`/api/chunks/${urgent}`)).body.urgency.slug).toBe('expedite');
  await expect(card.locator('app-urgency-pill')).toContainText('🔥');
  await expect(card.locator('app-urgency-pill')).toContainText('Expedite');
  const next = async () => (await api.get(`/api/runners/${runnerId}/next-claim`)).body.next?.chunk_id;
  await expect.poll(next).toBe(urgent);
  await expect(nextLine.getByTestId('runner-next-link')).toHaveAttribute('title', urgent);
  await expect(nextLine.locator('fleet-kit-urgency-pill')).toContainText('🔥');
  await expect(nextLine.locator('fleet-kit-urgency-pill')).toContainText('Expedite');

  // Retiring through admin transfers the still-ready work to the default urgency.
  await page.goto(`/admin/urgencies/${created.urgency_id}`);
  await page.getByTestId('admin-urgency-retire').click();
  await page.getByRole('button', { name: 'Retire and move chunks' }).click();
  await expect.poll(async () => (await api.get(`/api/chunks/${urgent}`)).body.urgency.slug).toBe(standard.slug);
  const retired = await api.get(`/api/urgencies/${created.urgency_id}`);
  expect(retired.body.retired).toBe(true);
  expect(await next()).toBe(first);
  await expect(nextLine.getByTestId('runner-next-link')).toHaveAttribute('title', first);
  await expect(nextLine.locator('fleet-kit-urgency-pill')).toHaveCount(0);
  await page.goto('/board');
  await expect(page.locator(`[data-col="ready"] [data-chunk="${urgent}"] app-urgency-pill`)).toHaveCount(0);
  // A real stepped host follows the changed next-claim ordering, rather than merely reading the explanation.
  await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${first}`)).body.status as string, (status) => status === 'running');
  expect((await api.get(`/api/chunks/${urgent}`)).body.status).toBe('ready');
});

import { expect, test } from '../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'wait' });
});

test('exhausted merge methods remain readable on a narrow chunk page', async ({ issue, page }) => {
  const { chunk } = await issue('merge methods exhausted');
  const detail = 'repo#42: allowed methods: merge, rebase, squash. ' +
    'merge: the forge rejected the merge commit because the pull request cannot be merged with this method; ' +
    'rebase: the commits cannot be rebased without conflicts; ' +
    'squash: the forge rejected the squash because the resulting commit would be empty.';
  // Supply the diagnostic at the wire boundary; delivery's live production of it is covered by service tests.
  await page.route(`**/api/chunks/${chunk.chunk_id}`, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, status: 'needs_human', escalation: {
      epoch: 1, cause: 'merge-method-refused', detail, takeover_command: '', wrapped_takeover_command: '',
    } } });
  });
  const viewportWidth = 390;
  await page.setViewportSize({ width: viewportWidth, height: 844 });
  await page.goto(`/board/chunk/${chunk.chunk_id}`);
  await expect(page.getByTestId('escalation-cause')).toHaveText('No allowed merge method could be used (epoch 1).');
  const explanation = page.getByTestId('escalation-detail');
  await expect(explanation).toHaveText(detail);
  await expect(explanation).toBeVisible();
  const fits = await explanation.evaluate((el, width) => {
    const rect = el.getBoundingClientRect();
    return rect.left >= 0 && rect.right <= width;
  }, viewportWidth);
  expect(fits).toBe(true);
});

test('unbroken escalation detail wraps without widening the page', async ({ issue, page }) => {
  const { chunk } = await issue('long escalation detail');
  const detail = 'LONG'.repeat(150);
  await page.route(`**/api/chunks/${chunk.chunk_id}`, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, status: 'needs_human', escalation: {
      epoch: 1, cause: 'merge-method-refused', detail, takeover_command: '', wrapped_takeover_command: '',
    } } });
  });
  for (const width of [320, 390, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`/board/chunk/${chunk.chunk_id}`);
    await expect(page.getByTestId('escalation-detail')).toHaveText(detail);
    expect(await page.evaluate<number>('document.documentElement.scrollWidth - document.documentElement.clientWidth')).toBe(0);
  }
});

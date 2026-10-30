import { expect, migratedChunk, test } from '../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('test_cross_graph_migration_renders_the_two_graph_timeline_and_explorer', async ({ api, issue, repository, runnerHosts, page }) => {
  test.setTimeout(120000);
  const { chunk } = await migratedChunk({ api, issue, repository, runnerHosts });
  await page.goto('/board');
  await expect(page.getByTestId('board-shell')).toBeVisible();
  const card = page.locator('[data-col="done"]').getByTestId('chunk-card');
  await expect(card).toHaveCount(1);
  await card.click();
  await expect(page.getByTestId('chunk-detail')).toBeVisible();
  await expect(page.getByTestId('detail-id')).toHaveText(chunk.chunk_id);
  await expect(page.getByTestId('history-migration-step')).toHaveCount(1);
  await expect(page.getByTestId('history-graph').first()).toBeVisible();
  await page.goto('/graphs');
  await expect(page.getByTestId('graph-explorer')).toBeVisible();
  for (const name of ['default-delivery', 'triage-delivery']) {
    await expect(page.locator(`[data-testid="graph-explorer-group"][data-name="${name}"]`)).toHaveCount(1);
  }
});

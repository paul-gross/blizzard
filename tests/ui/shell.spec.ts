import { expect, test } from '../harness/fixtures';

// exemplar: bzh:service-fixtures-only, bzh:service-isolation-earned.
// Reads the served board and writes nothing, so the shared hub is enough.
test.use({ isolation: 'common' });

test('test_the_served_bundle_renders_the_desktop_board_shell_at_the_wide_viewport', async ({ page, wideViewport }) => {
  await page.setViewportSize(wideViewport);
  await page.goto('/board');
  // The board's own shell, not the placeholder the hub serves when no bundle is built.
  await expect(page.getByTestId('board-shell')).toBeVisible();
  await expect(page.getByText('Frontend assets are not built yet')).toHaveCount(0);
});

test('test_the_served_bundle_renders_the_glance_board_at_the_narrow_viewport', async ({ page, narrowViewport }) => {
  await page.setViewportSize(narrowViewport);
  await page.goto('/board');
  await expect(page.getByTestId('glance-board')).toBeVisible();
  await expect(page.getByText('Frontend assets are not built yet')).toHaveCount(0);
});

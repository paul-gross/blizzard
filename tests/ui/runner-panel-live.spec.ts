import { expect, stepUntil, test } from '../harness/fixtures';

// The runner's own panel updates live over its event stream with no reload: a chunk the stepped host claims appears on
// the panel's board well inside the panel's one-minute poll backstop, so only a pushed frame can have put it there, and
// the same card reaches the Done lane once the host steps the chunk to done. It enrolls a runner host, so the test owns
// its tenant.
test.use({ isolation: 'fresh-tenant' });

// One runner node whose worker does nothing and whose judgement passes, so the chunk is done after its first step.
const GRAPH = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: 'pass\n',
      judgement: { prompt: "verdict('pass', 'done')\n", choices: { pass: { description: 'Done.', to: 'done' } } },
      retries: { max: 1, exhausted: 'escalate' },
    },
  },
});

// Well under the panel's 60s backstop poll, so a card arriving inside it was delivered by the event stream.
const PUSHED_WITHIN = 15000;

test('test_runner_panel_updates_live_over_sse_with_no_reload', async ({ api, issue, runnerHost, page, wideViewport }) => {
  test.setTimeout(120000);
  expect((await api.post('/api/graphs', { data: { definition_yaml: GRAPH } })).status).toBe(201);
  const { chunk } = await issue('panel live update');
  const chunkId = chunk.chunk_id;

  // The panel loads once and is never reloaded. Nothing is promoted yet, so its empty board is a settled pre-state.
  await page.setViewportSize(wideViewport);
  await page.goto(`${runnerHost.url}/board`);
  await expect(page.getByTestId('local-panel')).toBeVisible();
  const card = page.locator(`[data-testid="chunk-card"][data-chunk="${chunkId}"]`);
  const doneCard = page.getByTestId('lane-done').locator(`[data-testid="chunk-card"][data-chunk="${chunkId}"]`);
  await expect(card).toHaveCount(0);

  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
  expect((await runnerHost.step()).outcome).toBe('completed');
  await expect(card).toHaveCount(1, { timeout: PUSHED_WITHIN });

  // The host steps the chunk to done; the ended chunk stays on the board, in the Done lane, with no live worker.
  await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunkId}`)).body.status, (status) => status === 'done');
  await expect(doneCard).toHaveCount(1, { timeout: PUSHED_WITHIN });
  await expect(card.getByTestId('chunk-row-worker')).toHaveCount(0);
});

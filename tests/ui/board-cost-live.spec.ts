import { expect, test } from '../harness/fixtures';

// Cost and usage render on the board and the chunk detail and update live over the event stream, with no reload, as
// `usage.recorded` facts land: a billed figure, a cost-absent fact marking the total a lower bound, and a
// subscription-only chunk reading as an estimate. The header's spend-today figure is tenant-wide, and the spend is
// shipped through `fleet`, so the test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

interface Usage {
  cost_usd: number | null;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  cache_create_tokens?: number;
  estimated_cost_usd?: number;
}

test('test_board_renders_cost_and_updates_live_over_sse', async ({ api, fleet, issue, page, wideViewport }) => {
  const runner = await fleet('r1');
  const registered = await runner.post('/api/fleet/runners', {
    data: { name: 'r1', workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] },
  });
  expect(registered.status).toBe(201);

  /** A chunk issued, promoted and claimed by r1, so it derives running; its claimed node. */
  const running = async (title: string): Promise<[string, string]> => {
    const { chunk } = await issue(title);
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const claim = await runner.post('/api/fleet/routes', {
      data: { chunk_id: chunk.chunk_id, workspace_id: 'w1', environment_ids: ['e'] },
    });
    expect(claim.status).toBe(201);
    return [chunk.chunk_id, claim.body.envelope.node.node_id];
  };

  // `seq` is r1's own stream: the hub dedupes store-and-forward facts per (runner, seq), so a reused seq is dropped.
  const pushUsage = async (chunkId: string, nodeId: string, seq: number, usage: Usage): Promise<void> => {
    const payload = {
      chunk_id: chunkId,
      node_id: nodeId,
      epoch: 1,
      kind: 'spawn',
      model: 'claude-opus-4-8',
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_create_tokens: 0,
      ...usage,
    };
    const pushed = await runner.post('/api/fleet/events', { data: { facts: [{ seq, kind: 'usage.recorded', payload }] } });
    expect(pushed.status).toBe(200);
  };

  const [chunkId, nodeId] = await running('chunk — cost render');

  // The board loads once and is never reloaded.
  await page.setViewportSize(wideViewport);
  await page.goto('/');
  await expect(page.getByTestId('board-shell')).toBeVisible();
  const card = page.locator('[data-col="running"]').getByTestId('chunk-card');
  await expect(card).toHaveCount(1);

  // Before any usage, spend reads zero, not partial, and the card carries no cost figure.
  await expect(page.getByTestId('spend-today-value')).toHaveText('$0.00');
  await expect(card.getByTestId('card-cost')).toHaveCount(0);

  await pushUsage(chunkId, nodeId, 1, {
    cost_usd: 0.42,
    input_tokens: 1200,
    output_tokens: 800,
    cache_read_tokens: 300,
    cache_create_tokens: 100,
  });

  // The card figure and the header's spend-today both appear live off one fact, unmarked.
  await expect(card.getByTestId('card-cost')).toHaveText('$0.42');
  await expect(page.getByTestId('spend-today-value')).toHaveText('$0.42');

  // The detail dock: the header total, and all four token classes inline, one labelled row apiece.
  await card.click();
  await expect(page.getByTestId('chunk-detail')).toBeVisible();
  const total = page.getByTestId('cost-total-usd');
  await expect(total).toContainText('$0.42');
  await expect(total).not.toHaveAttribute('title', /.+/);
  await expect(page.getByTestId('fact-tokens-input')).toHaveText('1.2k');
  await expect(page.getByTestId('fact-tokens-output')).toHaveText('800');
  await expect(page.getByTestId('fact-tokens-cache-read')).toHaveText('300');
  await expect(page.getByTestId('fact-tokens-cache-creation')).toHaveText('100');

  // A cost-absent fact, as a crash or reap path ships, makes the total a lower bound, marked live everywhere it renders.
  await pushUsage(chunkId, nodeId, 2, { cost_usd: null });
  await expect(total).toHaveAttribute('title', /lower bound/);
  await expect(total).toContainText('$0.42+');
  await expect(card.getByTestId('card-cost')).toHaveText('$0.42+');
  await expect(page.getByTestId('spend-today-value')).toHaveText('$0.42+');

  // A second, subscription-only chunk: an estimate, no billed cost.
  const [estimateChunkId, estimateNodeId] = await running('chunk — cost estimate render');
  const estimateCard = page.locator(`[data-chunk="${estimateChunkId}"]`);
  await expect(estimateCard).toHaveCount(1);
  await expect(estimateCard.getByTestId('card-cost')).toHaveCount(0);

  await pushUsage(estimateChunkId, estimateNodeId, 3, {
    cost_usd: null,
    input_tokens: 900,
    output_tokens: 400,
    estimated_cost_usd: 0.07,
  });

  // Entirely estimated, the card reads as the estimate alone, marked `~`, never a separate billed figure.
  await expect(estimateCard.getByTestId('card-cost')).toHaveText('~$0.07');
  // Spend-today is fleet-wide: the first chunk's billed-and-partial $0.42 plus this estimate, both marks on one figure.
  await expect(page.getByTestId('spend-today-value')).toHaveText('~$0.49+');

  // This chunk's own detail is marked `~` and never `+`: an estimate-only row does not make a chunk partial.
  await estimateCard.click();
  await expect(total).toHaveText('~$0.07');
  await expect(total).not.toHaveAttribute('title', /.+/);
});

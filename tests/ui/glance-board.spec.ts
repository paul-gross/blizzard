import { expect, stepUntil, test } from '../harness/fixtures';

// The mobile glance board at the narrow viewport, where `/board` routes to the glance shell: while the chunks read is in
// flight the list shows loading, never empty, and a chunk with no billed cost shows its estimate, never a plain billed
// zero. Both cases read tenant-wide lists, and the first steps a runner host, so each test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

// A turn with no verdict, so every attempt fails the same way and the chunk escalates to needs_human; its `pass`
// choice is never taken.
const VERDICTLESS = 'pass\n';

const GRAPH = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: VERDICTLESS,
      judgement: { prompt: VERDICTLESS, choices: { pass: { description: 'Green.', to: 'done' } } },
      retries: { max: 1, exhausted: 'escalate' },
    },
  },
});

test('test_the_glance_board_shows_loading_before_rows_and_never_empty_on_a_populated_fleet', async ({ api, issue, runnerHost, page, narrowViewport }) => {
  test.setTimeout(120000);
  expect((await api.post('/api/graphs', { data: { definition_yaml: GRAPH } })).status).toBe(201);
  const { chunk } = await issue('glance board');
  const chunkId = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
  await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunkId}`)).body.status, (status) => status === 'needs_human');

  // Hold the chunks read open by capturing its route, so the loading state is observable rather than raced.
  const held: (() => Promise<void>)[] = [];
  let released = false;
  await page.route('**/api/chunks?**', async (route) => {
    if (released) await route.continue();
    else held.push(() => route.continue());
  });
  await page.setViewportSize(narrowViewport);
  // `commit`, not `load`: `load` waits on every in-flight request, including the held one.
  await page.goto('/board', { waitUntil: 'commit' });

  // The mobile shell, not the desktop board.
  await expect(page.getByTestId('glance-board')).toBeVisible();
  await expect(page.getByTestId('glance-list-loading')).toBeVisible();
  await expect(page.getByTestId('glance-list-empty')).toHaveCount(0);

  // Released, the card lands, and the empty copy never took its place in between.
  released = true;
  for (const release of held) await release();
  await expect(page.locator(`[data-testid="glance-card"][data-chunk="${chunkId}"]`)).toHaveCount(1);
  await expect(page.getByTestId('glance-list-loading')).toHaveCount(0);
  await expect(page.getByTestId('glance-list-empty')).toHaveCount(0);
});

test('test_the_glance_board_shows_a_cost_estimate_never_a_plain_billed_zero', async ({ api, fleet, issue, page, narrowViewport }) => {
  expect((await api.post('/api/graphs', { data: { definition_yaml: GRAPH } })).status).toBe(201);
  const { chunk } = await issue('chunk — glance cost estimate');
  const chunkId = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
  const runner = await fleet('r1');
  const registered = await runner.post('/api/fleet/runners', {
    data: { name: 'r1', workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] },
  });
  expect(registered.status).toBe(201);
  const claim = await runner.post('/api/fleet/routes', { data: { chunk_id: chunkId, workspace_id: 'w1', environment_ids: ['e'] } });
  expect(claim.status).toBe(201);
  const usage = {
    chunk_id: chunkId,
    node_id: claim.body.envelope.node.node_id,
    epoch: 1,
    kind: 'spawn',
    model: 'claude-opus-4-8',
    input_tokens: 900,
    output_tokens: 400,
    cache_read_tokens: 0,
    cache_create_tokens: 0,
    cost_usd: null,
    estimated_cost_usd: 0.07,
  };
  const pushed = await runner.post('/api/fleet/events', { data: { facts: [{ seq: 1, kind: 'usage.recorded', payload: usage }] } });
  expect(pushed.status).toBe(200);

  await page.setViewportSize(narrowViewport);
  await page.goto('/board');
  await expect(page.getByTestId('glance-board')).toBeVisible();

  // The spend panel and the running chunk's card both read the estimate alone, marked `~`, never a billed $0.00.
  await expect(page.getByTestId('glance-spend-value')).toHaveText('~$0.07');
  const card = page.locator(`[data-testid="glance-card"][data-chunk="${chunkId}"]`);
  await expect(card).toHaveCount(1);
  await expect(card.getByTestId('glance-cost')).toHaveText('~$0.07');
});

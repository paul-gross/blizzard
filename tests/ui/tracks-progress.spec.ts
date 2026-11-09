import { expect, stepUntil, test, type Wire } from '../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('tracks shows real chunk graph progress while switching layouts', async ({ api, issue, repository, runnerHosts, page, wideViewport, clock }) => {
  test.setTimeout(240_000);
  const repo = await repository('tracks-progress');
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const graph = { name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt: 'ask("Choose a direction", ["one", "two"])\n', judgement: {
      prompt: "verdict('pass', 'built')\n", choices: { pass: { description: 'Built', to: 'review' } },
    }, retries: { max: 1, exhausted: 'escalate' } },
    review: { executor: 'runner', prompt: 'pass\n', judgement: {
      prompt: "verdict('pass', 'reviewed')\n", choices: { pass: { description: 'Reviewed', to: 'done' } },
    }, retries: { max: 1, exhausted: 'escalate' } },
  } };
  expect((await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(graph) } })).status).toBe(201);
  const id = (await issue('Tracks progress real flow', { repository: repo })).chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  const status = await stepUntil(host, async () => (await api.get(`/api/chunks/${id}`)).body.status,
    (s) => ['waiting_on_human', 'done', 'needs_human', 'stopped'].includes(s));
  expect(status).toBe('waiting_on_human');

  const since = encodeURIComponent(new Date(clock.now().getTime() - 3600_000).toISOString());
  const timeline = await api.get<Wire.EnvTimelineView>(`/api/env-timeline?since=${since}`);
  expect(timeline.status).toBe(200);
  expect(timeline.body.chunks[id]).toBeDefined();
  const blocks = timeline.body.hosts.flatMap((h) => h.workspaces.flatMap((w) => w.environments.flatMap((e) => e.blocks)));
  expect(blocks.some((b) => b.chunk_id === id)).toBe(true);
  const board = await api.get<Wire.ChunksPageView>('/api/chunks?board_window=true');
  expect(board.status).toBe(200);
  const chunk = board.body.chunks.find((c) => c.chunk_id === id)!;
  expect(chunk.graph_progress.length).toBeGreaterThan(0);

  await page.setViewportSize(wideViewport);
  await page.goto('/board');
  await expect(page.locator(`[data-testid="chunk-card"][data-chunk="${id}"]`)).toBeVisible();
  await page.getByTestId('board-layout-list').click();
  await expect(page).toHaveURL(/layout=list/);
  await page.getByTestId('board-layout-tracks').click();
  await expect(page).toHaveURL(/layout=tracks/);
  await expect(page.getByTestId('tracks')).toBeVisible();
  const strip = page.getByTestId('tracks-present').getByTestId('node-strip');
  await expect(strip).toBeVisible();
  await expect(strip.locator('.block')).toHaveCount(chunk.graph_progress.length);
  for (const [index, node] of chunk.graph_progress.entries()) {
    await expect(strip.locator('.block').nth(index)).toHaveAttribute('title', `${node.name} · ${node.state}`);
  }
  await page.getByTestId('board-layout-board').click();
  await expect(page.locator(`[data-testid="chunk-card"][data-chunk="${id}"]`)).toBeVisible();
});

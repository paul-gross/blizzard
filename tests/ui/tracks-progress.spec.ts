import { expect, stepUntil, test, type Wire } from '../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('tracks shows real chunk graph progress while switching layouts', async ({ api, issue, repository, runnerHosts, page, wideViewport, clock }) => {
  test.setTimeout(240_000);
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const graph = { name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt: 'ask("Choose a direction", ["one", "two"])\n', judgement: {
      prompt: "verdict('pass', 'built')\n", choices: { pass: { description: 'Built', to: 'review' } },
    }, retries: { max: 1, exhausted: 'escalate' } },
    review: { executor: 'runner', prompt: 'pass\n', judgement: {
      prompt: "verdict('pass', 'reviewed')\n", choices: { pass: { description: 'Reviewed', to: 'gate' } },
    }, retries: { max: 1, exhausted: 'escalate' } },
    gate: { executor: 'runner', judgement: { by: 'human', choices: {
      approve: { description: 'Approve', to: 'done' }, reject: { description: 'Retry', to: 'build' },
    } } },
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
  // Generated choices have the same selection-follows-focus model as projected chips.
  const spans = page.getByRole('listbox', { name: 'Track span' });
  const selected = spans.getByRole('option', { selected: true });
  await expect(selected).toHaveCount(1);
  await selected.focus();
  await page.keyboard.press('ArrowRight');
  const focused = spans.locator('[role="option"]:focus');
  await expect(focused).toHaveCount(1);
  await expect(focused).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('Tab');
  await expect(spans.locator(':focus')).toHaveCount(0);
  const strip = page.getByTestId('tracks-present').getByTestId('node-strip');
  await expect(strip).toBeVisible();
  await expect(strip.locator('.block')).toHaveCount(chunk.graph_progress.length);
  for (const [index, node] of chunk.graph_progress.entries()) {
    await expect(strip.locator('.block').nth(index)).toHaveAttribute('title', `${node.name} · ${node.state}`);
  }
  await page.getByTestId('board-layout-board').click();
  const card = page.locator(`[data-testid="chunk-card"][data-chunk="${id}"]`);
  await expect(card).toBeVisible();
  await card.click();
  const toolbar = page.getByRole('toolbar', { name: 'Answers for Choose a direction' });
  const one = toolbar.getByRole('button', { name: 'one', exact: true });
  const two = toolbar.getByRole('button', { name: 'two', exact: true });
  await expect(toolbar.getByRole('option')).toHaveCount(0);
  await one.focus();
  await page.keyboard.press('ArrowRight');
  await expect(two).toBeFocused();
  await expect(two).not.toHaveAttribute('aria-selected');
  let answerRequests = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && /\/api\/questions\/[^/]+\/answers$/.test(new URL(request.url()).pathname)) answerRequests++;
  });
  const response = page.waitForResponse((r) => r.request().method() === 'POST' && /\/api\/questions\/[^/]+\/answers$/.test(new URL(r.url()).pathname));
  await page.keyboard.press('Space');
  expect((await response).ok()).toBe(true);
  await expect(page.getByTestId('open-question')).toHaveCount(0);
  await expect.poll(async () => (await api.get('/api/questions')).body.length).toBe(0);
  expect(answerRequests).toBe(1);

  // The resumed worker reaches a real human gate. Hold the resolution in flight to exercise the pending guard.
  expect(await stepUntil(host, async () => (await api.get(`/api/chunks/${id}`)).body.status,
    (s) => ['waiting_on_human', 'done', 'needs_human', 'stopped'].includes(s))).toBe('waiting_on_human');
  const decision = page.getByRole('toolbar', { name: 'Choices for gate gate' });
  const approve = decision.getByRole('button', { name: 'approve', exact: true });
  const reject = decision.getByRole('button', { name: 'reject', exact: true });
  await expect(approve).toBeVisible();
  await expect(decision.getByRole('option')).toHaveCount(0);
  await approve.focus();
  await page.keyboard.press('ArrowRight');
  await expect(reject).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(approve).toBeFocused();
  let resolutions = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let intercepted!: () => void;
  const requestHeld = new Promise<void>((resolve) => { intercepted = resolve; });
  await page.route('**/api/decisions/*/resolutions', async (route) => {
    resolutions++;
    intercepted();
    await held;
    await route.continue();
  });
  const resolved = page.waitForResponse((r) => r.request().method() === 'POST' && /\/api\/decisions\/[^/]+\/resolutions$/.test(new URL(r.url()).pathname));
  try {
    await page.keyboard.press('Enter');
    await requestHeld;
    await expect(approve).toHaveAttribute('aria-disabled', 'true');
    await page.keyboard.press('Enter');
    await page.keyboard.press('Space');
    await approve.click({ force: true });
    expect(resolutions).toBe(1);
  } finally {
    release();
  }
  expect((await resolved).ok()).toBe(true);
  await expect.poll(async () => (await api.get('/api/decisions')).body.decisions.length).toBe(0);
  expect((await api.get(`/api/chunks/${id}`)).body.decision.resolved_choice).toBe('approve');
  expect(resolutions).toBe(1);
});

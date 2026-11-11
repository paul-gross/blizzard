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
  // Hold the real stream's frames at the browser boundary. Releasing only the edited chunk's frame makes the
  // subsequent Next read attributable to that frame, not another event or a reconnect.
  await fleetPage.addInitScript(() => {
    const originalFetch = globalThis.fetch;
    const held: string[] = [];
    let controller: ReadableStreamDefaultController<Uint8Array>;
    const encoder = new TextEncoder();
    Object.assign(globalThis, { urgencyStream: { held, release: (frame: string) => controller.enqueue(encoder.encode(frame)) } });
    globalThis.fetch = async (...args) => {
      const response = await originalFetch(...args);
      if (!response.url.includes('/api/events/stream') || !response.body) return response;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      const body = new ReadableStream<Uint8Array>({
        start(stream) {
          controller = stream;
          void (async () => {
            while (true) {
              const { value, done } = await reader.read();
              if (done) break;
              buffer += decoder.decode(value, { stream: true });
              let end = buffer.indexOf('\n\n');
              while (end !== -1) {
                held.push(buffer.slice(0, end + 2));
                buffer = buffer.slice(end + 2);
                end = buffer.indexOf('\n\n');
              }
            }
            stream.close();
          })();
        },
        cancel: () => reader.cancel(),
      });
      return new Response(body, { status: response.status, headers: response.headers });
    };
  });
  await fleetPage.setViewportSize(wideViewport);
  // eslint-disable-next-line no-restricted-syntax -- This proof bounds real time since the Next query is created.
  const start = Date.now();
  await fleetPage.goto('/fleet');
  await fleetPage.locator('[data-testid^="fleet-card-hs_"]').first().click();
  const runner = fleetPage.locator(`[data-testid="fleet-runner"][data-runner="${runnerId}"]`);
  const nextLine = runner.getByTestId('runner-next-claim');
  await expect(nextLine.getByTestId('runner-next-link')).toHaveAttribute('title', first);
  const queueBefore = await api.get('/api/queue');
  expect(queueBefore.status).toBe(200);
  const rankedIds = queueBefore.body.entries.map((entry: { chunk_id: string }) => entry.chunk_id);
  expect(rankedIds).toEqual([first, urgent]);
  await fleetPage.evaluate(() => {
    (globalThis as unknown as { urgencyStream: { held: string[] } }).urgencyStream.held.length = 0;
  });
  await card.click();
  await page.getByTestId('chunk-urgency-menu').click();
  await page.getByRole('menuitemradio', { name: 'Expedite' }).click();
  await expect.poll(async () => (await api.get(`/api/chunks/${urgent}`)).body.urgency.slug).toBe('expedite');
  const queueAfter = await api.get('/api/queue');
  expect(queueAfter.status).toBe(200);
  expect(queueAfter.body.entries.map((entry: { chunk_id: string }) => entry.chunk_id)).toEqual(rankedIds);
  await expect(card.locator('app-urgency-pill')).toContainText('🔥');
  await expect(card.locator('app-urgency-pill')).toContainText('Expedite');
  const next = async () => (await api.get(`/api/runners/${runnerId}/next-claim`)).body.next?.chunk_id;
  await expect.poll(next).toBe(urgent);
  await expect.poll(async () => fleetPage.evaluate((id) => {
    const stream = (globalThis as unknown as { urgencyStream: { held: string[] } }).urgencyStream;
    return stream.held.find((item) => item.includes('event: chunk-changed') && item.includes(id)) ?? null;
  }, urgent)).not.toBeNull();
  await expect(nextLine.getByTestId('runner-next-link')).toHaveAttribute('title', first);
  await fleetPage.evaluate((id) => {
    const stream = (globalThis as unknown as { urgencyStream: { held: string[]; release: (frame: string) => void } }).urgencyStream;
    stream.release(stream.held.find((item) => item.includes('event: chunk-changed') && item.includes(id))!);
  }, urgent);
  await expect(nextLine.getByTestId('runner-next-link')).toHaveAttribute('title', urgent, { timeout: 10000 });
  // eslint-disable-next-line no-restricted-syntax -- The real polling interval must not have elapsed.
  expect(Date.now() - start).toBeLessThan(45000);
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
  // Release retirement's chunk frame too; the fleet tab's stream remains held.
  await expect.poll(async () => fleetPage.evaluate((id) => {
    const stream = (globalThis as unknown as { urgencyStream: { held: string[] } }).urgencyStream;
    return stream.held.filter((item) => item.includes('event: chunk-changed') && item.includes(id)).length;
  }, urgent)).toBeGreaterThan(1);
  await fleetPage.evaluate((id) => {
    const stream = (globalThis as unknown as { urgencyStream: { held: string[]; release: (frame: string) => void } }).urgencyStream;
    stream.release(stream.held.filter((item) => item.includes('event: chunk-changed') && item.includes(id)).at(-1)!);
  }, urgent);
  await expect(nextLine.getByTestId('runner-next-link')).toHaveAttribute('title', first);
  await expect(nextLine.locator('fleet-kit-urgency-pill')).toHaveCount(0);
  await page.goto('/board');
  await expect(page.locator(`[data-col="ready"] [data-chunk="${urgent}"] app-urgency-pill`)).toHaveCount(0);
  // A real stepped host follows the changed next-claim ordering, rather than merely reading the explanation.
  await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${first}`)).body.status as string, (status) => status === 'running');
  expect((await api.get(`/api/chunks/${urgent}`)).body.status).toBe('ready');
});

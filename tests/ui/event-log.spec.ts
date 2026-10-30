import { expect, test } from '../harness/fixtures';
import type { FleetClient, HubApi } from '../harness/fixtures';

// exemplar: bzh:service-fixtures-only, bzh:service-clients-only, bzh:service-isolation-earned, bzh:service-waits-on-the-system.
// The board's Events tab and activity rail, over the served bundle. Events come from runner clients posting to the fleet
// plane, as a runner would; the activity rail is driven by a mock runner.

const GRAPH = `name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: '# build'
    judgement:
      prompt: '# judge'
      choices:
        pass: {description: green, to: review}
    retries: {max: 1, exhausted: escalate}
  review:
    executor: runner
    prompt: '# review'
    session: fresh
    judgement:
      prompt: '# judge'
      choices:
        pass: {description: clean, to: deliver}
    retries: {max: 1, exhausted: escalate}
  deliver:
    executor: hub
    action: pass
    judgement:
      choices:
        success: {description: Delivered., to: done}
        failure: {description: Failed to deliver., to: build}
`;

/** The warning the hub records when a repository or work source names its forge by the old address fields. */
const OLD_FIELD_KIND = 'config-old-field-used';

/** Pushes one operational event as the runner `runner` would, and asserts the hub folded it. */
async function pushEvent(
  runner: FleetClient,
  event: { seq: number; severity: string; kind: string; chunkId: string; message: string },
): Promise<void> {
  const payload = { severity: event.severity, kind: event.kind, message: event.message, chunk_id: event.chunkId };
  const pushed = await runner.post('/api/fleet/events', { data: { facts: [{ seq: event.seq, kind: 'event.recorded', payload }] } });
  expect(pushed.status, JSON.stringify(pushed.body)).toBe(200);
  expect(pushed.body).toMatchObject({ applied: [event.seq], rejected: [] });
}

async function eventCount(api: HubApi): Promise<number> {
  const read = await api.get('/api/events');
  expect(read.status).toBe(200);
  return read.body.events.length;
}

async function activityCount(api: HubApi): Promise<number> {
  const read = await api.get('/api/activity');
  expect(read.status).toBe(200);
  return read.body.activity.length;
}

test.describe('on a fresh tenant', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('test_the_events_tab_renders_filters_and_updates_live_in_the_browser', async ({ page, api, fleet, issue }) => {
    const { chunk } = await issue('ui events');
    const chunkId = chunk.chunk_id;
    const first = await fleet('runner-ui');
    const second = await fleet('runner-two');
    // Seeded newest-last with a mixed-severity feed whose newest event is neither the most nor the least severe, so the
    // render order tells recency from severity: either severity order puts the critical row first, recency puts it last.
    const seeded = await eventCount(api);
    await pushEvent(first, { seq: 1, severity: 'critical', kind: 'worker-lost', chunkId, message: 'lost' });
    await pushEvent(first, { seq: 2, severity: 'info', kind: 'attempt-abandoned', chunkId, message: 'abandoned' });
    await pushEvent(first, { seq: 3, severity: 'warning', kind: 'attempt-failed', chunkId, message: 'retried' });

    await page.goto('/');
    await expect(page.getByTestId('board-shell')).toBeVisible();
    await page.getByTestId('nav-events').click();
    await expect(page.getByTestId('events-panel')).toBeVisible();
    await expect(page.getByTestId('events-row')).toHaveCount(seeded + 3);
    // Newest first: the warning row leads and the critical row, the oldest, trails.
    for (const [position, message] of ['retried', 'abandoned', 'lost'].entries()) {
      await expect(page.getByTestId('events-message').nth(position)).toHaveText(message);
    }

    // The severity filter narrows the list, then restores it.
    await page.getByTestId('events-filter-critical').click();
    await expect(page.getByTestId('events-row')).toHaveCount(1);
    await page.getByTestId('events-filter-all').click();
    await expect(page.getByTestId('events-row')).toHaveCount(seeded + 3);

    // One runner in the feed so far: nothing to narrow by, so the runner filter row is hidden.
    await expect(page.getByTestId('events-runner-filter')).toHaveCount(0);

    // A fresh event from a second runner arrives live over SSE, with no reload: the feed grows and the runner filter row
    // appears, with two runners to choose from.
    await pushEvent(second, { seq: 1, severity: 'critical', kind: 'worker-lost', chunkId, message: 'other runner' });
    await expect(page.getByTestId('events-row')).toHaveCount(seeded + 4);
    await expect(page.getByTestId('events-runner-filter')).toBeVisible();

    // The runner filter narrows to just the second runner's event, then restores. Its chips are keyed by the id the hub
    // minted for each runner.
    await page.getByTestId('events-runner-filter').click();
    await page.getByTestId(`events-runner-filter-${second.runnerId}`).click();
    await expect(page.getByTestId('events-row')).toHaveCount(1);
    await expect(page.getByTestId('events-message').first()).toHaveText('other runner');
    await page.getByTestId('events-runner-filter').click();
    await page.getByTestId('events-runner-filter-all').click();
    await expect(page.getByTestId('events-row')).toHaveCount(seeded + 4);

    // A row deep-links to its chunk, on the board.
    await page.getByTestId('events-chunk').first().click();
    await expect(page.getByTestId('board-shell')).toBeVisible();
  });

  test('test_the_events_grid_does_not_collapse_at_a_narrow_viewport', async ({ page, api, fleet, issue, narrowViewport }) => {
    await page.setViewportSize(narrowViewport);
    const { chunk } = await issue('narrow events');
    const runner = await fleet('runner-ui');
    const seeded = await eventCount(api);
    await pushEvent(runner, {
      seq: 1,
      severity: 'warning',
      kind: 'attempt-failed',
      chunkId: chunk.chunk_id,
      message: 'retry budget exhausted after the third consecutive verdict-less attempt in a row',
    });

    await page.goto('/events');
    await expect(page.getByTestId('events-row')).toHaveCount(seeded + 1);

    const box = await page.getByTestId('events-row').first().boundingBox();
    expect(box, 'the events row has no layout box').not.toBeNull();
    // A generous ceiling catches the collapse-class defect without pinning an exact pixel height (broken: ~848px; fixed: ~74px).
    expect(box!.height, 'the events grid collapsed at a narrow width').toBeLessThan(200);
    const noOverflow = await page.evaluate<boolean>('document.documentElement.scrollWidth <= document.documentElement.clientWidth');
    expect(noOverflow, 'the page gained horizontal scroll at a narrow viewport').toBe(true);
  });

  test('test_activity_rail_tracks_claim_transition_and_fact_burst_across_reload', async ({ page, api, issue, mockRunner, bearer }) => {
    const railMessages = (): Promise<string[]> => page.getByTestId('activity-message').allTextContents();
    expect((await api.post('/api/graphs', { data: { definition_yaml: GRAPH } })).status).toBe(201);
    const { chunk } = await issue('activity burst');
    const chunkId = chunk.chunk_id;
    // Counted once the chunk is filed: filing the first issue declares the tenant's repository, which records its own
    // rows, and the chunk's mint is already among them.
    const minted = await activityCount(api);
    expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
    const runner = await mockRunner('activity-pusher');
    expect((await runner.drive('register')).body.status).toBe(201);
    // The facts below push as the runner holding the lease, under its token.
    const pusher = await bearer(runner.token);

    await page.goto('/board');
    // The promotion; the runner's add has no durable row, so the rail drops it live too.
    await expect(page.getByTestId('activity-row')).toHaveCount(minted + 1);
    expect((await railMessages()).some((message) => message.includes('added to the fleet'))).toBe(false);
    const claim = await runner.drive('claim', { chunk_id: chunkId });
    expect(claim.body.claimed).toBe(true);
    await expect(page.getByTestId('activity-row')).toHaveCount(minted + 2);
    expect((await railMessages()).some((message) => message.includes('claimed'))).toBe(true);

    const completed = await runner.drive('complete', { chunk_id: chunkId, choice: 'pass' });
    expect(completed.body.response?.outcome).toBe('next');
    await expect(page.getByTestId('activity-row')).toHaveCount(minted + 3);
    const nodeId = (await api.get(`/api/chunks/${chunkId}`)).body.current_node_id;
    const facts = [
      { seq: 3, kind: 'lease.minted', payload: { chunk_id: chunkId, epoch: 2 } },
      {
        seq: 4,
        kind: 'usage.recorded',
        payload: {
          chunk_id: chunkId,
          node_id: nodeId,
          epoch: 2,
          kind: 'spawn',
          model: 'claude-opus-4-8',
          input_tokens: 100,
          output_tokens: 10,
          cache_read_tokens: 0,
          cache_create_tokens: 0,
          cost_usd: 0.03,
        },
      },
    ];
    const pushed = await pusher.post('/api/fleet/events', { data: { facts } });
    expect(pushed.status, JSON.stringify(pushed.body)).toBe(200);
    expect(pushed.body.applied).toEqual([3, 4]);
    await expect(page.getByTestId('spend-today-value')).toHaveText('$0.03');
    await expect(page.getByTestId('activity-row')).toHaveCount(minted + 3);

    const asked = await pusher.post('/api/fleet/events', {
      data: {
        facts: [
          {
            seq: 5,
            kind: 'question.asked',
            payload: {
              question_id: 'qn_activity',
              chunk_id: chunkId,
              node_id: nodeId,
              session_id: 'sess-activity',
              epoch: 2,
              question: 'Which path?',
              options: ['a', 'b'],
              asked_at: new Date().toISOString(),
            },
          },
        ],
      },
    });
    expect(asked.status, JSON.stringify(asked.body)).toBe(200);
    expect(asked.body.applied).toEqual([5]);
    await expect(page.getByTestId('activity-row')).toHaveCount(minted + 4);

    const read = (await api.get('/api/activity')).body.activity as { key: string; type: string; cause: string }[];
    const keys = read.map((row) => row.key);
    expect(keys).toHaveLength(minted + 4);
    expect(new Set(keys).size).toBe(keys.length);
    expect(new Set(read.filter((row) => row.type === 'chunk-changed').map((row) => row.cause))).toEqual(
      new Set(['minted', 'promoted', 'claimed', 'node-completed', 'question-asked']),
    );
    const liveMessages = await railMessages();
    expect(liveMessages).toHaveLength(keys.length);
    expect(liveMessages.filter((message) => message.includes('claimed'))).toHaveLength(1);
    await page.reload();
    await expect(page.getByTestId('activity-row')).toHaveCount(keys.length);
    expect((await railMessages()).filter((message) => message.includes('claimed'))).toHaveLength(1);
    expect((await api.get('/api/activity', { params: { limit: 201 } })).status).toBe(422);
  });
});

test.describe('on a fresh hub', () => {
  test.use({ isolation: 'fresh-hub' });

  test('test_the_rail_survives_a_reload_with_no_duplicate_or_missing_rows', async ({ page, api, fleet, issue, freshHub }) => {
    const { chunk: chunkA } = await issue('reload seam A');
    await issue('reload seam B');
    // Counted once both chunks are filed, their mints and the tenant's declaration rows among them.
    const filed = await activityCount(api);
    const runner = await fleet('runner-reload-seam');
    await pushEvent(runner, { seq: 1, severity: 'warning', kind: 'attempt-failed', chunkId: chunkA.chunk_id, message: 'a probed operational event' });

    await page.goto('/');
    await expect(page.getByTestId('board-shell')).toBeVisible();
    await expect(page.getByTestId('activity-panel')).toBeVisible();
    // Facts already landed before this subscribes. The add of the runner that pushed the event has no durable row, so it
    // never renders: the event is the one new row.
    await expect(page.getByTestId('activity-row')).toHaveCount(filed + 1);
    const firstLoad = await page.getByTestId('activity-message').allTextContents();
    expect(firstLoad).toHaveLength(filed + 1);

    // The hub's facts are durable but its event broker's replay ring is not: the restarted hub starts a fresh, empty one
    // over the same store.
    await freshHub.stop();
    await freshHub.start();
    await page.reload();
    await expect(page.getByTestId('activity-panel')).toBeVisible();
    // The fresh broker's replay tail is empty: only the `GET /api/activity` backfill can repopulate these rows.
    await expect(page.getByTestId('activity-row')).toHaveCount(filed + 1);
    const reloaded = await page.getByTestId('activity-message').allTextContents();

    // The event-logged row carries the same severity and kind whichever source renders it, so its text matches exactly.
    const eventMessage = firstLoad.find((message) => message.includes('attempt-failed'));
    expect(eventMessage).toBeDefined();
    expect(reloaded.find((message) => message.includes('attempt-failed'))).toBe(eventMessage);

    // Live-rendered rows carry the `→` transition arrow; backfill-rendered rows are the bare chunk ref. The flip proves
    // the backfill path, not leftover replay.
    const mintRows = (messages: string[]): string[] =>
      messages.filter((message) => message !== eventMessage && !message.includes(OLD_FIELD_KIND));
    const firstMints = mintRows(firstLoad);
    const reloadMints = mintRows(reloaded);
    expect(firstMints).toHaveLength(2);
    expect(reloadMints).toHaveLength(2);
    expect(firstMints.every((message) => message.includes('→'))).toBe(true);
    expect(reloadMints.every((message) => !message.includes('→'))).toBe(true);
    expect(new Set(reloadMints)).toEqual(new Set(firstMints.map((message) => message.split(' ')[0])));
  });
});

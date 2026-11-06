import { type Clock, expect, type HubApi, test, type Wire } from '../../harness/fixtures';

// The cadence sweep minting due errands over a real hub, on the controlled clock: a moment mints its run, a moment
// missed while the hub was down is owed one run, however many moments passed, a ten-day spacing holds a schedule's
// moments between runs, a live run holds its errand, and a promoting chore's run lands ready where another's is held.
// Every claim about time moves the clock and steps the `cadence` sweep through its pass route; nothing waits.

const DAY = 24 * 60 * 60;
const GRAPH = 'garden-routine';

interface Rhythm {
  schedule?: { cron: string; zone: string };
  spacing?: string;
  anchor?: string;
}

interface Chore {
  key: string;
  promote?: boolean;
  scopes?: string[];
}

/** A cadence keeping `chores` of the `sweep` routine on `rhythm`. */
async function cadence(api: HubApi, projectSlug: string, rhythm: Rhythm, chores: Chore[] = [{ key: 'plain' }]): Promise<void> {
  const created = await api.post(`/api/projects/${projectSlug}/cadences`, {
    data: { slug: 'nightly', ...rhythm, chores: chores.map((chore) => ({ routine: 'sweep', ...chore })) },
  });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
}

/** Every routine run minted from `since` up to the clock's instant. */
async function runs(api: HubApi, clock: Pick<Clock, 'now'>, since: Date): Promise<Wire.RunRowView[]> {
  const until = new Date(clock.now().getTime() + 1000);
  const listed = await api.get(`/api/runs?since=${since.toISOString()}&until=${until.toISOString()}`);
  expect(listed.status, JSON.stringify(listed.body)).toBe(200);
  return listed.body as Wire.RunRowView[];
}

async function stop(api: HubApi, run: Wire.RunRowView | undefined): Promise<void> {
  expect(run, 'no run to stop').toBeDefined();
  expect((await api.post(`/api/chunks/${run!.chunk_id}/stop`, { data: { by: 'e2e-test' } })).status).toBe(202);
}

/** The first instant at `hour` o'clock UTC strictly after `after`. */
function nextAt(after: Date, hour: number): Date {
  const at = new Date(after.getTime());
  at.setUTCHours(hour, 0, 0, 0);
  if (at.getTime() <= after.getTime()) at.setUTCDate(at.getUTCDate() + 1);
  return at;
}

test.describe('a cadence on a fresh hub with cadences on', () => {
  test.use({ isolation: 'fresh-hub', clockMode: 'controlled', hubConfig: { flags: { cadences: true } } });

  test.beforeEach(async ({ api, project }) => {
    const created = await api.post(`/api/projects/${project.slug}/routines`, {
      data: { slug: 'sweep', graph_name: GRAPH, default_scope_slug: 'sweep' },
    });
    expect(created.status, JSON.stringify(created.body)).toBe(201);
  });

  test('a moment mints its errand once, and none before it', async ({ api, clock, project }) => {
    const start = clock.now();
    await cadence(api, project.slug, { schedule: { cron: '0 20 * * *', zone: 'UTC' } });
    const moment = nextAt(new Date(start.getTime() + 120_000), 20);

    await clock.set(new Date(moment.getTime() - 60_000));
    await api.sweep('cadence');
    expect(await runs(api, clock, start)).toEqual([]);

    await clock.set(moment);
    await api.sweep('cadence');
    await api.sweep('cadence');
    expect((await runs(api, clock, start)).map((run) => [run.routine_name, run.scope_slug])).toEqual([['sweep', 'sweep']]);
  });

  test('a moment passed while the hub was down is minted once the hub is back', async ({ api, clock, project, testHub }) => {
    const start = clock.now();
    await cadence(api, project.slug, { schedule: { cron: '0 20 * * *', zone: 'UTC' } });
    const lifecycle = testHub.lifecycle!;

    await lifecycle.stop();
    await clock.set(new Date(nextAt(new Date(start.getTime() + 120_000), 20).getTime() + 3600_000));
    await lifecycle.start();
    await api.sweep('cadence');

    expect(await runs(api, clock, start)).toHaveLength(1);
  });

  test('a week of missed moments mints one run, and the next comes only at the next moment', async ({ api, clock, project }) => {
    const start = clock.now();
    await cadence(api, project.slug, { schedule: { cron: '0 20 * * *', zone: 'UTC' } });

    await clock.advance(7 * DAY);
    await api.sweep('cadence');
    const minted = await runs(api, clock, start);
    expect(minted).toHaveLength(1);
    const [first] = minted;
    await stop(api, first);
    await api.sweep('cadence');
    expect(await runs(api, clock, start)).toHaveLength(1);

    await clock.set(nextAt(clock.now(), 20));
    await api.sweep('cadence');
    expect(await runs(api, clock, start)).toHaveLength(2);
  });

  test('a ten-day spacing beside a nightly moment mints every tenth night', async ({ api, clock, project }) => {
    const start = clock.now();
    await cadence(api, project.slug, { schedule: { cron: '0 20 * * *', zone: 'UTC' }, spacing: '10d', anchor: 'scheduled' });
    const first = nextAt(new Date(start.getTime() + 120_000), 20);

    await clock.set(first);
    await api.sweep('cadence');
    const [run] = await runs(api, clock, start);
    await stop(api, run);
    for (let night = 1; night < 10; night += 1) {
      await clock.set(new Date(first.getTime() + night * DAY * 1000));
      await api.sweep('cadence');
    }
    expect(await runs(api, clock, start), 'a night inside the spacing minted a run').toHaveLength(1);

    await clock.set(new Date(first.getTime() + 10 * DAY * 1000));
    await api.sweep('cadence');
    expect(await runs(api, clock, start)).toHaveLength(2);
  });

  test('a live run holds its errand until it is stopped', async ({ api, clock, project }) => {
    const start = clock.now();
    await cadence(api, project.slug, { spacing: '1h' });

    await api.sweep('cadence');
    const [live] = await runs(api, clock, start);
    await clock.advance(2 * 3600);
    await api.sweep('cadence');
    expect(await runs(api, clock, start), 'a second run was minted while the first was live').toHaveLength(1);

    await stop(api, live);
    await api.sweep('cadence');
    expect(await runs(api, clock, start)).toHaveLength(2);
  });

  test('a promoting chore lands its run ready, and another chore holds its run', async ({ api, clock, project }) => {
    const start = clock.now();
    await cadence(api, project.slug, { spacing: '1h' }, [{ key: 'every' }, { key: 'held', scopes: ['sweep'], promote: false }]);

    await api.sweep('cadence');

    expect((await runs(api, clock, start)).map((run) => run.outcome).sort()).toEqual(['not_ready', 'ready']);
  });
});

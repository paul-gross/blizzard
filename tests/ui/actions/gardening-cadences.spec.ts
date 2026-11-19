import type { Page } from '@playwright/test';

import { type Clock, expect, test, type Wire } from '../../harness/fixtures';

// The Cadences tab on a fresh hub, on the controlled clock: a cadence created on the board, a chore over every linked
// scope added on its detail, a second chore naming a scope the project lacks and one it keeps but never linked to the
// routine — the lacking one has no errand to stop, so the unlinked one's errand is the one stopped — and the next
// moments moving with the clock once the board reads the cadence again. The browser renders local time, so it runs in
// UTC; every instant asserted is read from the clock, and nothing waits on the machine's time.
test.use({ isolation: 'fresh-hub', clockMode: 'controlled', timezoneId: 'UTC' });

const GRAPH = 'garden-routine';
const DAY = 24 * 60 * 60;

/** The zone box is pick-only: search the list for the zone, then pick its row. */
async function pickZone(page: Page, testid: string, zone: string): Promise<void> {
  await page.getByTestId(testid).fill(zone);
  await page.getByRole('option').filter({ has: page.locator('.label', { hasText: new RegExp(`^${zone}$`) }) }).click();
}

/** The first instant at 20:00 UTC strictly after `after`. */
function nextEvening(after: Date): Date {
  const at = new Date(after.getTime());
  at.setUTCHours(20, 0, 0, 0);
  if (at.getTime() <= after.getTime()) at.setUTCDate(at.getUTCDate() + 1);
  return at;
}

/** The five evenings after the clock's instant, as the board writes an instant in UTC: `2026/11/06 20:00:00`. */
function eveningsAfter(clock: Pick<Clock, 'now'>): string[] {
  const first = nextEvening(clock.now());
  return Array.from({ length: 5 }, (_, n) => {
    const at = new Date(first.getTime() + n * DAY * 1000);
    const pad = (v: number): string => `${v}`.padStart(2, '0');
    return `${at.getUTCFullYear()}/${pad(at.getUTCMonth() + 1)}/${pad(at.getUTCDate())} 20:00:00`;
  });
}

test('test_gardening_cadences_browser', async ({ api, clock, page, project, wideViewport }) => {
  const routine = await api.post(`/api/projects/${project.slug}/routines`, {
    data: { slug: 'sweep', graph_name: GRAPH, default_scope_slug: 'north' },
  });
  expect(routine.status, JSON.stringify(routine.body)).toBe(201);
  const routineId = (routine.body as Wire.RoutineView).routine_id;
  for (const slug of ['north', 'south']) {
    const scope = await api.post(`/api/projects/${project.slug}/scopes`, { data: { slug, description: `the ${slug} beds` } });
    expect(scope.status, JSON.stringify(scope.body)).toBe(201);
    expect((await api.put(`/api/routines/${routineId}/scopes/${slug}`)).status).toBe(204);
  }

  await page.setViewportSize(wideViewport);
  await page.goto(`/p/${project.slug}/gardening/cadences`);
  // The hub runs with the flag off: cadences are kept and shown, and the list says they mint nothing.
  await expect(page.getByTestId('gardening-cadences-flag-off')).toBeVisible();
  await expect(page.getByTestId('gardening-cadences-empty')).toBeVisible();

  // Create the cadence on the board.
  await page.getByTestId('gardening-cadence-new').click();
  await page.getByTestId('gardening-cadence-create-slug').fill('evening');
  await page.getByTestId('gardening-cadence-create-name').fill('Evening sweep');
  await page.getByTestId('gardening-cadence-create-cron').fill('0 20 * * *');
  await pickZone(page, 'gardening-cadence-create-zone', 'UTC');
  await page.getByTestId('gardening-cadence-create-save').click();
  await expect(page).toHaveURL(new RegExp(`/p/${project.slug}/gardening/cadences/evening$`));
  await expect(page.getByTestId('gardening-cadence-rhythm-evening')).toContainText('0 20 * * * UTC');
  const panel = page.getByTestId('gardening-cadence-panel');
  await expect(panel.getByTestId('gardening-cadence-panel-no-chores')).toBeVisible();
  await expect(panel.getByTestId('gardening-cadence-panel-moment')).toHaveText(eveningsAfter(clock).map((t) => new RegExp(t)));

  // Add a chore over every linked scope on its detail.
  await page.getByTestId('gardening-cadence-chore-add').click();
  await page.getByTestId('gardening-chore-add-key').fill('every');
  await page.getByTestId('gardening-chore-add-routine').fill('sweep');
  await page.getByTestId('gardening-chore-add-scopes').fill('all');
  await page.getByTestId('gardening-chore-add-save').click();
  await expect(page.getByTestId('gardening-cadence-chore-every')).toContainText('over all scopes');
  for (const slug of ['north', 'south']) {
    await expect(page.getByTestId(`gardening-cadence-errand-every-${slug}`)).toContainText('waiting');
  }

  // A third scope the project keeps but the routine never links.
  const west = await api.post(`/api/projects/${project.slug}/scopes`, { data: { slug: 'west', description: 'the west beds' } });
  expect(west.status, JSON.stringify(west.body)).toBe(201);

  // A second chore naming that scope and one the project lacks, seeded over the API: both need attention, and the
  // errand over the unlinked scope is stopped.
  const listed = await api.get(`/api/projects/${project.slug}/cadences`);
  expect(listed.status, JSON.stringify(listed.body)).toBe(200);
  const cadence = (listed.body as Wire.CadenceView[]).find((c) => c.slug === 'evening')!;
  const chores = cadence.chores.map(({ key, routine, scopes, mode, promote, note }) => ({ key, routine, scopes, mode, promote, note }));
  const edited = await api.patch(`/api/cadences/${cadence.cadence_id}`, {
    data: { chores: [...chores, { key: 'lost', routine: 'sweep', scopes: ['west', 'nowhere'] }] },
  });
  expect(edited.status, JSON.stringify(edited.body)).toBe(200);

  await page.reload();
  const stopped = page.getByTestId('gardening-cadence-errand-lost-west');
  await expect(stopped).toContainText('stopped');
  await expect(stopped).toContainText('the scope is not linked to the routine');
  await expect(page.getByTestId('gardening-cadence-errand-lost-nowhere')).toHaveCount(0);
  await expect(page.getByTestId('gardening-cadence-panel-attention')).toContainText('the scope does not exist');
  await expect(page.getByTestId('gardening-cadence-counts-evening')).toContainText('2 waiting');
  await expect(page.getByTestId('gardening-cadence-counts-evening')).toContainText('1 stopped');

  // Two days on, the board reads the cadence again and its next moments have moved with the clock.
  const before = eveningsAfter(clock);
  await clock.advance(2 * DAY);
  const after = eveningsAfter(clock);
  expect(after[0]).not.toBe(before[0]);
  await page.reload();
  await expect(panel.getByTestId('gardening-cadence-panel-moment')).toHaveText(after.map((t) => new RegExp(t)));
});

test('test_gardening_cadence_form_refusals_browser', async ({ api, page, project, wideViewport }) => {
  const taken = await api.post(`/api/projects/${project.slug}/cadences`, {
    data: { slug: 'evening', schedule: { cron: '0 20 * * *', zone: 'UTC' } },
  });
  expect(taken.status, JSON.stringify(taken.body)).toBe(201);
  const cadenceId = (taken.body as Wire.CadenceView).cadence_id;

  const posts: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/cadences')) posts.push(request.url());
  });
  const field = (testid: string) => page.locator('fleet-kit-field', { has: page.getByTestId(testid) });

  await page.setViewportSize(wideViewport);
  await page.goto(`/p/${project.slug}/gardening/cadences/evening`);
  await expect(page.getByTestId('gardening-cadence-panel')).toBeVisible();

  // A zone with no cron is refused on the board, and nothing is sent.
  await page.getByTestId('gardening-cadence-new').click();
  await expect(field('gardening-cadence-create-slug')).toContainText('*');
  await page.getByTestId('gardening-cadence-create-slug').fill('evening');
  await pickZone(page, 'gardening-cadence-create-zone', 'UTC');
  await page.getByTestId('gardening-cadence-create-save').click();
  await expect(field('gardening-cadence-create-zone')).toContainText('A schedule needs both a cron and a zone');
  expect(posts).toHaveLength(0);

  // A slug the project already holds is the hub's to refuse, and the refusal lands on the slug.
  await page.getByTestId('gardening-cadence-create-cron').fill('0 21 * * *');
  await page.getByTestId('gardening-cadence-create-save').click();
  await expect(field('gardening-cadence-create-slug')).toContainText('evening');
  await expect(page.getByTestId('gardening-cadence-create-slug')).toBeFocused();
  await expect(page.getByTestId('gardening-cadence-create-slug')).toHaveValue('evening');
  await expect(page.getByTestId('gardening-cadence-create-cron')).toHaveValue('0 21 * * *');
  expect(posts).toHaveLength(1);
  await page.getByTestId('gardening-cadence-create-cancel').click();

  // An edit raced by another write is refused at the form, keeping what was typed; the other write stands.
  await page.getByTestId('gardening-cadence-panel-edit').click();
  await page.getByTestId('gardening-cadence-edit-name').fill('Evening sweep');
  const raced = await api.patch(`/api/cadences/${cadenceId}`, { data: { spacing: '1d' } });
  expect(raced.status, JSON.stringify(raced.body)).toBe(200);
  await page.getByTestId('gardening-cadence-edit-save').click();
  await expect(page.getByTestId('gardening-cadence-edit-line')).toBeVisible();
  await expect(page.getByTestId('gardening-cadence-edit-name')).toHaveValue('Evening sweep');
  const held = await api.get(`/api/projects/${project.slug}/cadences`);
  const stored = (held.body as Wire.CadenceView[]).find((c) => c.slug === 'evening')!;
  expect(stored.name).toBe('evening');
  expect(stored.spacing_minutes).toBe(24 * 60);

  // Cancel, then Edit again, starts from the fresh read and saves cleanly.
  await page.getByTestId('gardening-cadence-edit-cancel').click();
  await page.getByTestId('gardening-cadence-panel-edit').click();
  await page.getByTestId('gardening-cadence-edit-name').fill('Evening sweep');
  await page.getByTestId('gardening-cadence-edit-save').click();
  await expect(page.getByTestId('gardening-cadence-edit')).toHaveCount(0);
  await expect(page.getByTestId('gardening-cadence-row-evening')).toContainText('Evening sweep');
  const saved = await api.get(`/api/projects/${project.slug}/cadences`);
  expect((saved.body as Wire.CadenceView[]).find((c) => c.slug === 'evening')!.name).toBe('Evening sweep');
});

test('test_gardening_chore_form_refusals_browser', async ({ api, page, project, wideViewport }) => {
  const made = await api.post(`/api/projects/${project.slug}/cadences`, {
    data: {
      slug: 'evening',
      schedule: { cron: '0 20 * * *', zone: 'UTC' },
      chores: [{ key: 'nightly', routine: 'sweep', scopes: 'all' }],
    },
  });
  expect(made.status, JSON.stringify(made.body)).toBe(201);
  const cadenceId = (made.body as Wire.CadenceView).cadence_id;

  const patches: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'PATCH' && request.url().includes('/cadences/')) patches.push(request.url());
  });
  const field = (testid: string) => page.locator('fleet-kit-field', { has: page.getByTestId(testid) });
  const storedChores = async () => {
    const held = await api.get(`/api/projects/${project.slug}/cadences`);
    return (held.body as Wire.CadenceView[]).find((c) => c.slug === 'evening')!.chores;
  };

  await page.setViewportSize(wideViewport);
  await page.goto(`/p/${project.slug}/gardening/cadences/evening`);
  await expect(page.getByTestId('gardening-cadence-panel')).toBeVisible();

  // A blank routine is refused on its field, which takes focus, and nothing is sent.
  await page.getByTestId('gardening-cadence-chore-add').click();
  await page.getByTestId('gardening-chore-add-key').fill('weekly');
  await page.getByTestId('gardening-chore-add-save').click();
  await expect(field('gardening-chore-add-routine')).toContainText('required');
  await expect(page.getByTestId('gardening-chore-add-routine')).toBeFocused();
  expect(patches).toHaveLength(0);

  // A key the cadence already holds is the hub's to refuse, and the refusal lands on the key.
  await page.getByTestId('gardening-chore-add-key').fill('nightly');
  await page.getByTestId('gardening-chore-add-routine').fill('sweep');
  await page.getByTestId('gardening-chore-add-save').click();
  await expect(page.getByTestId('gardening-chore-add-key')).toBeFocused();
  await expect(page.getByTestId('gardening-chore-add-key')).toHaveValue('nightly');
  expect(patches).toHaveLength(1);
  await page.getByTestId('gardening-chore-add-cancel').click();

  // An edit raced by another write is refused on the form line, keeping what was typed; the other write stands.
  await page.getByTestId('gardening-cadence-chore-edit-nightly').click();
  await page.getByTestId('gardening-chore-edit-nightly-note').fill('typed');
  const raced = await api.patch(`/api/cadences/${cadenceId}`, { data: { spacing: '1d' } });
  expect(raced.status, JSON.stringify(raced.body)).toBe(200);
  await page.getByTestId('gardening-chore-edit-nightly-save').click();
  await expect(page.getByTestId('gardening-chore-edit-nightly-line')).toBeVisible();
  await expect(page.getByTestId('gardening-chore-edit-nightly-note')).toHaveValue('typed');
  expect((await storedChores()).map((c) => c.note)).toEqual([null]);

  // Cancel, then Edit again, starts from the fresh read and saves cleanly.
  await page.getByTestId('gardening-chore-edit-nightly-cancel').click();
  await page.getByTestId('gardening-cadence-chore-edit-nightly').click();
  await page.getByTestId('gardening-chore-edit-nightly-note').fill('typed');
  await page.getByTestId('gardening-chore-edit-nightly-save').click();
  await expect(page.getByTestId('gardening-chore-edit-nightly')).toHaveCount(0);
  expect((await storedChores()).map((c) => c.note)).toEqual(['typed']);
});

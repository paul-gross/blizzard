import { expect, test, type Wire } from '../../harness/fixtures';

// The Routines tab on a fresh hub: a routine created on the board and each inline edit on its detail, through the shared
// form path — a blank required field shows its error before any request, a hub refusal lands on its field with what was
// typed kept, a taken slug on the form line, and an edit made against a routine someone else changed since it opened is
// refused at form level.
test.use({ isolation: 'fresh-hub' });

const GRAPH = 'garden-routine';

test('test_gardening_routines_browser', async ({ api, page, project, wideViewport, narrowViewport }) => {
  const scope = await api.post(`/api/projects/${project.slug}/scopes`, { data: { slug: 'north', description: 'the north beds' } });
  expect(scope.status, JSON.stringify(scope.body)).toBe(201);

  await page.setViewportSize(wideViewport);
  await page.goto(`/p/${project.slug}/gardening/routines`);
  const posts: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith(`/api/projects/${project.slug}/routines`)) posts.push(request.url());
  });

  // A blank required field shows its error before any request is sent.
  await page.getByTestId('gardening-routine-new').click();
  const create = page.getByTestId('gardening-routine-create');
  await create.getByTestId('gardening-routine-create-slug').fill('nightly');
  await create.getByTestId('gardening-routine-create-save').click();
  await expect(create).toContainText(/required/i);
  expect(posts).toHaveLength(0);

  // A hub refusal focuses its field and keeps what was typed: a malformed scope slug.
  await create.getByTestId('gardening-routine-create-graph').fill(GRAPH);
  await create.getByTestId('gardening-routine-create-scope').fill('Bad Scope!');
  await create.getByTestId('gardening-routine-create-save').click();
  await expect(create.getByTestId('gardening-routine-create-scope')).toBeFocused();
  await expect(create.getByTestId('gardening-routine-create-slug')).toHaveValue('nightly');
  await expect(create.getByTestId('gardening-routine-create-graph')).toHaveValue(GRAPH);

  // The first routine is created, blank name and all.
  await create.getByTestId('gardening-routine-create-scope').fill('north');
  await create.getByTestId('gardening-routine-create-save').click();
  await expect(page).toHaveURL(new RegExp(`/p/${project.slug}/gardening/routines/nightly$`));

  // A taken slug shows on the form line.
  await page.getByTestId('gardening-routine-new').click();
  await create.getByTestId('gardening-routine-create-slug').fill('nightly');
  await create.getByTestId('gardening-routine-create-graph').fill(GRAPH);
  await create.getByTestId('gardening-routine-create-scope').fill('north');
  await create.getByTestId('gardening-routine-create-save').click();
  await expect(create.getByTestId('gardening-routine-create-line')).toBeVisible();
  await create.getByTestId('gardening-routine-create-cancel').click();
  await page.goto(`/p/${project.slug}/gardening/routines/nightly`);

  // An unresolved graph on an inline edit lands on the graph field, and the draft is kept.
  const panel = page.getByTestId('gardening-routine-panel');
  await panel.getByTestId('gardening-routine-panel-edit').click();
  const graph = panel.getByTestId('gardening-routine-panel-graph-input');
  await graph.fill('');
  await panel.getByTestId('gardening-routine-panel-graph-save').click();
  await expect(panel.getByTestId('gardening-routine-panel-graph')).toContainText(/required/i);
  await graph.fill('no-such-graph');
  await panel.getByTestId('gardening-routine-panel-graph-save').click();
  await expect(graph).toBeFocused();
  await expect(graph).toHaveValue('no-such-graph');

  // A stale edit is refused at form level: the routine changes after the edit opened.
  const routines = await api.get(`/api/routines`);
  const routine = (routines.body as Wire.RoutineView[]).find((r) => r.slug === 'nightly')!;
  const renamed = await api.patch(`/api/routines/${routine.routine_id}`, { data: { name: 'Nightly sweep' } });
  expect(renamed.status, JSON.stringify(renamed.body)).toBe(200);
  // A refusal on the field blocks a resubmit until the value changes, so retype before pressing Set.
  await graph.fill('other-no-such-graph');
  await panel.getByTestId('gardening-routine-panel-graph-save').click();
  await expect(panel.getByTestId('gardening-routine-panel-graph-line')).toBeVisible();
  await expect(graph).toHaveValue('other-no-such-graph');

  // The forms hold at the narrow viewport.
  await page.setViewportSize(narrowViewport);
  await expect(panel.getByTestId('gardening-routine-panel-graph-save')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await panel.getByTestId('gardening-routine-panel-graph-cancel').click();
  await page.goto(`/p/${project.slug}/gardening/routines`);
  await page.getByTestId('gardening-routine-new').click();
  await expect(page.getByTestId('gardening-routine-create-save')).toBeVisible();
});

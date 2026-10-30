import { expect, test } from '../../harness/fixtures';

// The gardening run dialog, opened off the selected routine's own panel: it offers only the routine's related set — its
// own default plus a scope linked ahead of time, no free-text mint — steers a never-swept scope away from delta, and
// lands on a confirmation naming a real chunk id and linking to the board. The routine's graph names a test action, so
// the spec rides the actions hub; the run mints a queued chunk that no runner executes. It mints routines and scopes,
// so the test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

test('test_gardening_run_dialog_browser', async ({ api, page, wideViewport }, testInfo) => {
  const suffix = testInfo.testId.replace(/[^a-z0-9]/gi, '').slice(0, 8).toLowerCase();
  const graphName = `gardening-graph-${suffix}`;
  const routineName = `gardening-routine-${suffix}`;
  const defaultSlug = `gardening-default-${suffix}`;
  const relatedSlug = `gardening-related-${suffix}`;
  const unrelatedSlug = `gardening-unrelated-${suffix}`;

  // The smallest valid graph — one hub-executed node — enough for a routine to name, never reached.
  const definition = [
    `name: ${graphName}`,
    'entry: work',
    'nodes:',
    '  work:',
    '    executor: hub',
    '    action: test.answer',
    '    with: {choice: landed}',
    '    judgement:',
    '      choices:',
    '        landed: {description: Done., to: done}',
    '        failure: {description: Failed., to: done}',
    '',
  ].join('\n');
  expect((await api.post('/api/graphs', { data: { definition_yaml: definition } })).status).toBe(201);

  const routine = await api.post('/api/routines', {
    data: {
      name: routineName,
      graph_name: graphName,
      default_scope_slug: defaultSlug,
      default_model: [],
      default_effort: null,
    },
  });
  expect(routine.status).toBe(201);
  const routineId = routine.body.routine_id;

  // The dialog mints no scope of its own, so a related-but-not-default scope must already exist and be linked; an
  // unrelated one, never linked, proves the picker narrows to the related set rather than every live scope.
  expect((await api.post('/api/scopes', { data: { slug: relatedSlug, description: 'a related weed patch' } })).status)
    .toBe(201);
  expect((await api.put(`/api/routines/${routineId}/scopes/${relatedSlug}`)).status).toBe(204);
  expect(
    (await api.post('/api/scopes', { data: { slug: unrelatedSlug, description: 'not this routine’s own' } })).status,
  ).toBe(201);

  await page.setViewportSize(wideViewport);

  // The routine list selects; the panel triggers the run.
  await page.goto('/gardening/routines');
  const row = page.getByTestId(`gardening-routine-row-${routineName}`);
  await expect(row).toBeVisible();
  await row.click();
  await expect(page.getByTestId('gardening-routine-record')).toContainText(routineName);

  await page.getByTestId('gardening-routine-run').click();
  const dialog = page.getByTestId('gardening-run-dialog');
  await expect(dialog).toBeVisible();
  await expect(page.getByTestId('run-dialog-title')).toContainText(routineName);

  // The dialog offers only the routine's related set.
  await expect(page.getByTestId(`run-scope-option-${defaultSlug}`)).toBeVisible();
  await expect(page.getByTestId(`run-scope-option-${relatedSlug}`)).toBeVisible();
  await expect(page.getByTestId(`run-scope-option-${unrelatedSlug}`)).toHaveCount(0);
  await expect(page.getByTestId('run-scope-option-new')).toHaveCount(0);

  // The never-swept default scope steers to full.
  await page.getByTestId(`run-scope-option-${defaultSlug}`).click();
  await expect(page.getByTestId('run-mode-never-swept')).toBeVisible();
  await expect(page.getByTestId('run-mode-delta')).toBeDisabled();

  // Switching to the related, linked scope and submitting.
  await page.getByTestId(`run-scope-option-${relatedSlug}`).click();
  const submit = page.getByTestId('run-dialog-submit');
  await expect(submit).toBeEnabled();
  await submit.click();

  // The confirmation names the real chunk id and links to the board, rendering no board of its own.
  await expect(page.getByTestId('run-confirmation')).toBeVisible();
  const chunkIdEl = page.getByTestId('run-confirmation-chunk-id');
  await expect(chunkIdEl).toHaveText(/^\s*ch_\S+\s*$/);
  const chunkId = (await chunkIdEl.textContent())!.trim();
  // A tenant holding one project opens gardening under that project's lens, and the link keeps it.
  await expect(page.getByTestId('run-confirmation-board-link')).toHaveAttribute(
    'href',
    `/p/default/board/chunk/${chunkId}`,
  );
  await expect(dialog.locator('app-board-shell, [data-testid="board"]')).toHaveCount(0);

  // The run really landed against the scope the operator picked — a hub read, not merely a rendered claim.
  const run = await api.get(`/api/runs/${chunkId}`);
  expect(run.status).toBe(200);
  expect(run.body.scope_slug).toBe(relatedSlug);

  // Done closes the dialog back to the routines list.
  await page.getByTestId('run-dialog-done').click();
  await expect(dialog).toHaveCount(0);
  await expect(row).toBeVisible();
});

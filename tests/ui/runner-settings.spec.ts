import { expect, test } from '../harness/fixtures';

// The runner panel's Settings tab against a stepped real host, with authentication off so the page's session is the
// implicit superuser. An edit through the dialog writes a revision the `in_force` line reads as pending; the host's next
// pass takes it into force, and the line follows with no reload. A rebuild that fails shows its revision and error while
// the older revision stays in force, and unsetting the key clears it. It enrolls a runner host, so the test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

// Well inside the panel's poll of a record whose revision is not yet in force.
const TAKES_HOLD_WITHIN = 15000;

test('test_a_settings_edit_takes_hold_at_the_next_pass_and_a_failed_rebuild_shows_beside_the_older_revision', async ({ runnerHost, page, wideViewport }) => {
  test.setTimeout(120000);
  const runner = runnerHost.runners['runner-local']!;
  const p = `settings-${runner.id}`;
  const inForce = page.getByTestId(`${p}-in-force`);
  const pending = page.getByTestId(`${p}-in-force-pending`);
  const failed = page.getByTestId(`${p}-in-force-failed`);

  /** Opens `key`'s edit dialog, enters `text`, and saves, the dialog closing once the write is answered. */
  const edit = async (key: string, text: string): Promise<void> => {
    await page.getByTestId(`${p}-edit-${key}`).click();
    await page.getByTestId('setting-edit-value').fill(text);
    await page.getByTestId('setting-edit-save').click();
    await expect(page.getByTestId('setting-edit-save')).toHaveCount(0);
  };
  /** The runner's own settings read. */
  const read = async () => {
    const answer = await runner.settings();
    expect(answer.status).toBe(200);
    return answer.body;
  };

  // A first pass puts the host's boot revision in force, so the line opens on a settled pre-state.
  expect((await runnerHost.step()).outcome).toBe('completed');
  await page.setViewportSize(wideViewport);
  await page.goto(`${runnerHost.url}/board`);
  await page.getByTestId('nav-settings').click();
  await expect(page.getByTestId('settings-page')).toBeVisible();
  const before = await read();
  await expect(inForce).toContainText(`running revision ${before.in_force.revision}`);
  await expect(pending).toHaveCount(0);

  // 1. The edit writes a revision the line reads as pending.
  await edit('throttle.budget.usd', '7.5');
  const written = await read();
  expect(written.revision).toBeGreaterThan(before.revision);
  await expect(pending).toContainText(`revision ${written.revision} written`);
  await expect(page.getByTestId(`${p}-value-throttle.budget.usd`)).toHaveText('7.5');

  // 2. The next pass takes it into force; the line follows with no reload, and the runner's read agrees.
  expect((await runnerHost.step()).outcome).toBe('completed');
  await expect(inForce).toContainText(`running revision ${written.revision}`, { timeout: TAKES_HOLD_WITHIN });
  await expect(pending).toHaveCount(0);
  expect((await read()).in_force.revision).toBe(written.revision);

  // 3. A prompt file that is missing fails its rebuild: the line shows that revision and its error, the older in force.
  await edit('runner_prompt_file', 'missing-prompt');
  const broken = (await read()).revision;
  expect((await runnerHost.step()).outcome).toBe('completed');
  await expect(failed).toContainText(`revision ${broken} failed to take hold`, { timeout: TAKES_HOLD_WITHIN });
  await expect(failed).toContainText('missing-prompt');
  await expect(inForce).toContainText(`running revision ${written.revision}`);
  const failure = (await read()).in_force;
  expect(failure.revision).toBe(written.revision);
  expect(failure.failed?.revision).toBe(broken);

  // 4. Unsetting it takes hold at the next pass and clears the failure.
  await page.getByTestId(`${p}-edit-runner_prompt_file`).click();
  await page.getByTestId('setting-edit-reset').click();
  await expect(page.getByTestId('setting-edit-save')).toHaveCount(0);
  const recovered = (await read()).revision;
  expect(recovered).toBeGreaterThan(broken);
  expect((await runnerHost.step()).outcome).toBe('completed');
  await expect(inForce).toContainText(`running revision ${recovered}`, { timeout: TAKES_HOLD_WITHIN });
  await expect(failed).toHaveCount(0);
  expect((await read()).in_force.failed).toBeNull();
});

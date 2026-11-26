import { expect, test } from '../harness/fixtures';

// Proposal acceptance through the board's Accept dialog, against open proposals seeded on the test's own hub: a mint
// with an edited body and a chosen urgency, and a decline blocked at its reason and then sent with one. Every gesture
// is read back from the hub. It mints an urgency and a work item, so the test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

type Closure = { closure: string; reason: string | null; item_outcome: string; source: string | null; ref: string | null };

test('test_gardening_proposal_accept_dialog_mints_and_declines', async ({ api, page, project, wideViewport }) => {
  expect((await api.post('/api/urgencies', { data: { slug: 'expedite', name: 'Expedite' } })).status).toBe(201);
  const seed = async (title: string) => {
    const created = await api.post(`/api/projects/${project.slug}/garden-proposals`, {
      data: { title, class: 'handoff', body: `the ${title} body` },
    });
    expect(created.status).toBe(201);
    return created.body.proposal_id as string;
  };
  const minted = await seed('mint me');
  const declined = await seed('decline me');
  const closure = async (id: string) => (await api.get(`/api/garden-proposals/${id}`)).body.closure as Closure | null;

  await page.setViewportSize(wideViewport);
  const dialog = page.getByTestId('gardening-proposal-accept-dialog');
  const open = async (id: string) => {
    await page.goto(`/p/${project.slug}/gardening/proposals/${id}`);
    await page.getByTestId('gardening-proposal-accept').click();
    await expect(dialog).toBeVisible();
  };
  const body = dialog.getByTestId('proposal-accept-body-input');
  const reason = dialog.getByTestId('proposal-accept-reason-input');
  const accept = dialog.getByTestId('kit-form-save');

  // Mint: the body arrives prefilled from the proposal; the edit and the picked urgency are what the hub records.
  await open(minted);
  await expect(dialog.getByTestId('proposal-accept-mode-mint')).toBeChecked();
  await expect(body).toHaveValue('the mint me body');
  await body.fill('an edited work item body');
  await dialog.getByTestId('proposal-accept-urgency').click();
  await page.getByRole('option', { name: 'Expedite' }).click();
  await accept.click();
  await expect(dialog).toHaveCount(0);
  await expect.poll(async () => (await closure(minted))?.item_outcome).toBe('minted');
  const mintedClosure = (await closure(minted))!;
  expect(mintedClosure).toMatchObject({ closure: 'accepted', reason: null });
  const item = await api.get(`/api/work-sources/${mintedClosure.source}/items/${mintedClosure.ref}`);
  expect(item.status).toBe(200);
  expect(JSON.stringify(item.body)).toContain('an edited work item body');
  const chunks = (await api.get('/api/chunks')).body.chunks as { urgency: { slug: string } | null }[];
  expect(chunks.map((chunk) => chunk.urgency?.slug)).toEqual(['expedite']);

  // Decline: a blank reason is held at its field and nothing is sent; with a reason it closes with no work item.
  await open(declined);
  await dialog.getByTestId('proposal-accept-mode-decline').click();
  await expect(body).toHaveCount(0);
  await accept.click();
  await expect(dialog.getByTestId('proposal-accept-reason-field').getByRole('alert')).toBeVisible();
  await expect(reason).toBeFocused();
  expect(await closure(declined)).toBeNull();
  await reason.fill('already covered elsewhere');
  await accept.click();
  await expect(dialog).toHaveCount(0);
  await expect.poll(async () => (await closure(declined))?.item_outcome).toBe('declined');
  expect(await closure(declined)).toMatchObject({ closure: 'accepted', reason: 'already covered elsewhere', ref: null });
});

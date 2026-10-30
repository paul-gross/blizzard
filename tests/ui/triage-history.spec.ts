import { expect, test } from '../harness/fixtures';

// A packaged triage migration selects its producing step on the board's Node history tab: the authored-edge migration
// row names its origin step, and selecting it opens that step's transcript and artifact, at the wide viewport and at
// the narrow one, where the detail closes back to the list. It syncs graphs and ships as a runner through `fleet`, so
// the test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

test('test_triage_migration_selects_transcript_and_artifact', async ({ api, fleet, issue, page, wideViewport, narrowViewport }) => {
  const sync = await api.post('/api/graphs/sync');
  expect(sync.status).toBe(200);
  expect(sync.body.ok).toBe(true);
  const { chunk } = await issue('triage migration history');
  const chunkId = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);

  const runner = await fleet('r1');
  const registered = await runner.post('/api/fleet/runners', {
    data: { name: 'r1', workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] },
  });
  expect(registered.status).toBe(201);
  const claim = await runner.post('/api/fleet/routes', { data: { chunk_id: chunkId, workspace_id: 'w1', environment_ids: ['e'] } });
  expect(claim.status).toBe(201);
  const nodeId: string = claim.body.envelope.node.node_id;
  expect(claim.body.envelope.node.node_name).toBe('triage');

  const lease = await runner.post('/api/fleet/events', {
    data: { facts: [{ seq: 1, kind: 'lease.minted', payload: { chunk_id: chunkId, epoch: 1 } }] },
  });
  expect(lease.status).toBe(200);
  const completed = await runner.post(`/api/fleet/chunks/${chunkId}/completions`, {
    data: {
      choice: 'basic',
      epoch: 1,
      from_node_id: nodeId,
      artifacts: [{ name: 'triage-findings', kind: 'asset', content: 'basic lane rationale' }],
    },
  });
  expect(completed.status).toBe(200);
  expect(completed.body.outcome).toBe('migrated');
  const shipped = await runner.post('/api/fleet/transcripts', {
    data: {
      records: [{
        seq: 1,
        segment_id: 'sg_triage',
        chunk_id: chunkId,
        node_id: nodeId,
        epoch: 1,
        spawn_generation: 1,
        turn_range_start: 0,
        turn_range_end: 1,
        final: true,
        normalizer_version: 'v1',
        harness_version: 'claude-code-1.0',
        turns: [{
          index: 0,
          kind: 'asst',
          timestamp: null,
          text: 'triage chose basic lane',
          tool: null,
          thinking_redacted: false,
          sidechain: null,
          truncated: false,
        }],
      }],
    },
  });
  expect(shipped.status).toBe(200);
  expect(shipped.body.applied).toEqual([1]);

  // The migration and the artifact both carry the triage step's origin.
  const detail = await api.get(`/api/chunks/${chunkId}`);
  expect(detail.status).toBe(200);
  expect(detail.body.migrations[0].source).toBe('authored-edge');
  expect([detail.body.migrations[0].from_node_id, detail.body.migrations[0].epoch]).toEqual([nodeId, 1]);
  expect(detail.body.artifacts).toContainEqual(expect.objectContaining({ name: 'triage-findings', node_id: nodeId, epoch: 1 }));

  const failures: string[] = [];
  page.on('response', (response) => {
    if (response.status() >= 400 && response.url().includes('/api/')) failures.push(`${response.status()} ${response.url()}`);
  });

  for (const viewport of [wideViewport, narrowViewport]) {
    await page.setViewportSize(viewport);
    await page.goto(`/board/chunk/${chunkId}?tab=node-history`);
    const row = page.getByTestId('selection-migration-step');
    await expect(row).toHaveAttribute('data-step-key', `${nodeId}:1`);
    await expect(row).toHaveAttribute('role', 'button');
    await row.click();
    await expect(page).toHaveURL(new RegExp(`[?&]step=${nodeId}(?:%3A|:)1`));
    await expect(page.getByTestId('node-history-transcript-body')).toContainText('triage chose basic lane');
    await expect(page.getByTestId('node-history-artifact-content')).toContainText('basic lane rationale');
    await expect(page.getByTestId('node-history-artifact-key')).toContainText('triage-findings');
    if (viewport === narrowViewport) {
      await page.getByTestId('node-history-back').click();
      await expect(page.getByTestId('selection-migration-step')).toBeVisible();
    }
  }
  expect(failures).toEqual([]);
});

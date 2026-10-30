import { expect, test } from '../harness/fixtures';

// exemplar: bzh:service-fixtures-only, bzh:service-clients-only, bzh:service-isolation-earned.
// The two-graph history of a migrated chunk on the served board. A mock runner drives the migration; no host steps.
test.use({ isolation: 'fresh-tenant' });

/** `default-delivery`, so ingest pins it, whose `build` migrates to `triage-delivery`. */
const SOURCE = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: '# build',
      judgement: {
        prompt: '# judge',
        choices: { migrate: { description: 'Hand off to triage-delivery.', to: 'graph:triage-delivery' } },
      },
      retries: { max: 1, exhausted: 'escalate' },
    },
  },
});

/** `triage-delivery`, whose `build` name-matches the source's and finishes the chunk. */
const TARGET = JSON.stringify({
  name: 'triage-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: '# triage',
      judgement: { prompt: '# judge', choices: { pass: { description: 'Committed and green.', to: 'done' } } },
      retries: { max: 1, exhausted: 'escalate' },
    },
  },
});

test('test_the_migrated_chunks_two_graph_history_renders_on_the_served_board', async ({ page, api, issue, mockRunner }) => {
  // The target first, so the source's cross-graph choice resolves at mint without a warning.
  const target = await api.post('/api/graphs', { data: { definition_yaml: TARGET } });
  expect(target.status).toBe(201);
  expect((await api.post('/api/graphs', { data: { definition_yaml: SOURCE } })).status).toBe(201);
  const { chunk } = await issue('migration');
  const chunkId = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);

  const runner = await mockRunner('runner-migrate');
  expect((await runner.drive('register')).body.status).toBe(201);
  expect((await runner.drive('claim', { chunk_id: chunkId })).body.claimed).toBe(true);
  const migrated = await runner.drive('complete', { chunk_id: chunkId, choice: 'migrate' });
  expect(migrated.body.response?.outcome, JSON.stringify(migrated.body)).toBe('migrated');
  expect((await runner.drive('claim', { chunk_id: chunkId })).body.claimed).toBe(true);
  const finished = await runner.drive('complete', { chunk_id: chunkId, choice: 'pass' });
  expect(finished.body.response?.outcome, JSON.stringify(finished.body)).toBe('done');
  const detail = (await api.get(`/api/chunks/${chunkId}`)).body;
  expect(detail.status, JSON.stringify(detail)).toBe('done');
  expect(detail.graph_id).toBe(target.body.graph_id);

  // The migrated chunk lands `done`: open its detail dock from that column.
  await page.goto('/');
  await expect(page.getByTestId('board-shell')).toBeVisible();
  const doneCard = page.locator('[data-col="done"]').getByTestId('chunk-card');
  await expect(doneCard).toHaveCount(1);
  await doneCard.click();
  await expect(page.getByTestId('chunk-detail')).toBeVisible();
  await expect(page.getByTestId('detail-id')).toHaveText(chunkId);

  // The timeline weaves in the migration step and labels each row with the graph it happened in, with no raw-id degradation.
  await expect(page.getByTestId('history-migration-step')).toHaveCount(1);
  await expect(page.getByTestId('history-graph').first()).toBeVisible();

  // The standalone /graphs explorer still lists both graphs of the history.
  await page.goto('/graphs');
  await expect(page.getByTestId('graph-explorer')).toBeVisible();
  for (const name of ['default-delivery', 'triage-delivery']) {
    await expect(page.locator(`[data-testid="graph-explorer-group"][data-name="${name}"]`)).toHaveCount(1);
  }
});

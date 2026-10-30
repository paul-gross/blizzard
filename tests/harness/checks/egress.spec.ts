import * as fs from 'node:fs';
import * as path from 'node:path';
import { test as plain } from '@playwright/test';
import { expect, test } from '../fixtures';
import { assertProfilesSound } from '../profiles';
import { requireHubOptions } from '../tenancy';

// A fresh hub exporting Parquet: a mock runner works one node-step, an egress pass writes it, and the fixture reads the
// chunk's row off the dictionary's view through the docs' own load recipe.

const GRAPH = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: '# build',
      judgement: { prompt: '# judge', choices: { pass: { description: 'green', to: 'deliver' } } },
      retries: { max: 1, exhausted: 'escalate' },
    },
    deliver: {
      executor: 'hub',
      action: 'pass',
      judgement: {
        choices: { success: { description: 'Delivered.', to: 'done' }, failure: { description: 'Failed to deliver.', to: 'build' } },
      },
    },
  },
});

plain.describe('the ${egress} placeholder', () => {
  plain('is admitted in a test’s hubConfig and refused in a shared profile', () => {
    expect(() => requireHubOptions('fresh-hub', { egress: { directory: '${egress}' } }, {})).not.toThrow();
    const dir = fs.mkdtempSync(path.join(process.env.BLIZZARD_TMPDIR ?? '/tmp', 'egress-profile-'));
    const file = path.join(dir, 'profile.toml');
    fs.writeFileSync(file, '[egress]\ndirectory = "${egress}"\n');
    expect(() => assertProfilesSound([{ project: 'service-hub-x', name: 'x', dir, file }], [])).toThrow(/egress\.directory: \$\{egress\} is not a placeholder/);
    fs.rmSync(dir, { recursive: true });
  });
});

test.describe('the egress reader on a fresh hub exporting Parquet', () => {
  test.use({
    isolation: 'fresh-hub',
    hubConfig: { egress: { directory: '${egress}', format: 'parquet', settle_seconds: 0n, min_free_bytes: 0n } },
  });

  test('reads a closed step’s row off steps_newest and through a docs recipe', async ({ api, issue, mockRunner, egress }) => {
    // The export's first pass only anchors its cursors, so a step is written by the pass after it closes.
    await api.sweep('egress', { allTenants: true });
    expect((await api.post('/api/graphs', { data: { definition_yaml: GRAPH } })).status).toBe(201);
    const { chunk } = await issue('egress reader over a real hub');
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const runner = await mockRunner('runner-egress');
    expect((await runner.drive('register')).body.status).toBe(201);
    expect((await runner.drive('claim', { chunk_id: chunk.chunk_id })).body.claimed).toBe(true);
    const completed = await runner.drive('complete', { chunk_id: chunk.chunk_id, choice: 'pass' });
    expect(completed.body.response?.outcome, JSON.stringify(completed.body)).toBe('hub_node_taken');

    await api.sweep('egress', { allTenants: true });

    const rows = await egress.query('steps', `SELECT step_key, node_name, step_kind FROM steps_newest WHERE step_key LIKE '${chunk.chunk_id}/%'`);
    expect(rows).toEqual([{ step_key: `${chunk.chunk_id}/1`, node_name: 'build', step_kind: 'runner' }]);

    const cost = await egress.recipe('cost-by-node-by-day');
    expect(cost.map((row) => row.node_name)).toContain('build');
  });
});

test.describe('the egress reader on a fresh hub exporting the default format', () => {
  test.use({
    isolation: 'fresh-hub',
    hubConfig: { egress: { directory: '${egress}', settle_seconds: 0n, min_free_bytes: 0n } },
  });

  test('reads NDJSON files, renders every copy of a row as text, and holds the manifests and schemas', async ({ api, issue, mockRunner, egress }) => {
    await api.sweep('egress', { allTenants: true });
    expect((await api.post('/api/graphs', { data: { definition_yaml: GRAPH } })).status).toBe(201);
    const { chunk } = await issue('egress reader over ndjson');
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const runner = await mockRunner('runner-egress-ndjson');
    expect((await runner.drive('register')).body.status).toBe(201);
    expect((await runner.drive('claim', { chunk_id: chunk.chunk_id })).body.claimed).toBe(true);
    expect((await runner.drive('complete', { chunk_id: chunk.chunk_id, choice: 'pass' })).body.response?.outcome).toBe('hub_node_taken');
    await api.sweep('egress', { allTenants: true });

    const rows = await egress.query('steps', `SELECT step_key, node_name FROM steps_newest WHERE step_key LIKE '${chunk.chunk_id}/%'`);
    expect(rows).toEqual([{ step_key: `${chunk.chunk_id}/1`, node_name: 'build' }]);
    const texts = await egress.rowTexts('steps');
    expect(texts.some((text) => text.includes(`${chunk.chunk_id}/1`))).toBe(true);
    const documents = egress.documents();
    expect(documents.manifests.length).toBeGreaterThan(0);
    expect(documents.manifests.some((text) => text.includes('"steps"'))).toBe(true);
    expect(documents.schemas.length).toBeGreaterThan(0);
  });
});

test.describe('the egress reader below fresh-hub', () => {
  test.use({ isolation: 'common' });
  test.fail('is refused, naming fresh-hub', async ({ egress }) => {
    void egress;
  });
});

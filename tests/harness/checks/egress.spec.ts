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

const bareLocal = (at: Date): string => {
  const two = (value: number): string => String(value).padStart(2, '0');
  return `${at.getFullYear()}-${two(at.getMonth() + 1)}-${two(at.getDate())}T${two(at.getHours())}:${two(at.getMinutes())}:${two(at.getSeconds())}`;
};

test.describe('a fresh hub restarted over its record', () => {
  test.use({
    isolation: 'fresh-hub',
    hubConfig: { egress: { directory: '${egress}', settle_seconds: 0n, min_free_bytes: 0n } },
  });

  test('refuses an unsound overlay, serves a sound one, keeps its record, and the reader keeps a backfill apart', async ({ api, cli, issue, mockRunner, freshHub, egress }) => {
    await api.sweep('egress', { allTenants: true });
    expect((await api.post('/api/graphs', { data: { definition_yaml: GRAPH } })).status).toBe(201);
    const { chunk } = await issue('egress overlay');
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const runner = await mockRunner('runner-egress-overlay');
    expect((await runner.drive('register')).body.status).toBe(201);
    expect((await runner.drive('claim', { chunk_id: chunk.chunk_id })).body.claimed).toBe(true);
    expect((await runner.drive('complete', { chunk_id: chunk.chunk_id, choice: 'pass' })).body.response?.outcome).toBe('hub_node_taken');
    const since = new Date(Math.floor(Date.now() / 1000) * 1000 - 5000);
    await api.sweep('egress', { allTenants: true });
    expect(egress.exports('steps')).toBe(true);

    await freshHub.stop();
    // An overlay the hub would read leniently is refused, naming the key, and leaves the hub stopped.
    await expect(freshHub.start({ egress: { directroy: '${egress}-second' } })).rejects.toThrow(/egress\.directroy/);
    await freshHub.start({ egress: { directory: '${egress}-second', format: 'parquet' } });
    expect((await api.get(`/api/chunks/${chunk.chunk_id}`)).status).toBe(200);

    const second = egress.at(`${egress.directory}-second`, 'parquet');
    for (const dataset of ['steps', 'invocations']) {
      const reset = await cli(['hub', 'egress', 'reset', '--dataset', dataset, '--to', bareLocal(since)]);
      expect(reset.code, reset.stderr).toBe(0);
    }
    await expect.poll(async () => {
      await api.sweep('egress', { allTenants: true });
      return second.exports('steps');
    }, { message: 'the overlay’s directory to hold the record', timeout: 60_000 }).toBe(true);
    const live = await second.copies('steps');
    expect(live.map((row) => row.chunk_id)).toContain(chunk.chunk_id);

    const mark = Date.now();
    await expect.poll(() => Date.now(), { timeout: 15_000 }).toBeGreaterThan(mark + 1100);
    const until = new Date(Math.floor(Date.now() / 1000) * 1000);
    const zoned = (at: Date): string => at.toISOString().replace(/\.\d{3}Z$/, 'Z');
    const backfill = await cli(['hub', 'egress', 'backfill', '--since', zoned(since), '--until', zoned(until)]);
    expect(backfill.code, backfill.stderr).toBe(0);
    const backfilled = await second.copies('steps', { files: 'backfill' });
    const liveOnly = await second.copies('steps', { files: 'live' });
    expect(backfilled.length).toBeGreaterThan(0);
    expect((await second.copies('steps')).length).toBe(backfilled.length + liveOnly.length);
  });
});

test.describe('the egress reader below fresh-hub', () => {
  test.use({ isolation: 'common' });
  test.fail('is refused, naming fresh-hub', async ({ egress }) => {
    void egress;
  });
});

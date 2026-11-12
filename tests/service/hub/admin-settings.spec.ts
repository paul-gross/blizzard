import * as fs from 'node:fs';
import { parse } from 'smol-toml';
import { expect, test } from '../../harness/fixtures';

// These routes mutate hub-wide state, so no other test may share the hub.
test.use({ isolation: 'fresh-hub' });
const path = '/api/admin/settings';

test('implicit operator sees conflicts and refusals without partial writes', async ({ api }) => {
  const initial = await api.get(path);
  expect(initial.status).toBe(200);
  const revision = initial.body.revision as number;
  const write = (match: number, data: object) => api.patch(path, { headers: { 'If-Match': String(match) }, data });
  expect((await write(revision, { set: { action_pass_seconds: 5 } })).status).toBe(200);
  expect((await write(revision, { set: { action_pass_seconds: 6 } })).status).toBe(409);
  for (const [data, code] of [
    [{ set: { host: 'invalid' } }, 'setting_not_live'],
    [{ set: { not_a_setting: 1 } }, 'setting_unknown'],
    [{ set: { action_pass_seconds: -1 } }, 'settings_invalid'],
  ] as const) {
    const response = await write(revision + 1, data);
    expect(response.status).toBe(422);
    expect(response.body.code).toBe(code);
  }
  const current = await api.get(path);
  expect(current.body.revision).toBe(revision + 1);
  expect(current.body.settings).toEqual(expect.arrayContaining([expect.objectContaining({ key: 'action_pass_seconds', value: 5, written_by: 'operator' })]));
});

test('apply previews atomically and CLI TOML export reapplies unchanged', async ({ api, cli }, testInfo) => {
  const initial = await api.get(path);
  const revision = initial.body.revision as number;
  const baseline = (await api.get(path + '/export')).body;
  const history = (await api.get(path + '/changes')).body.changes;
  const document = { action_pass_seconds: 6, transcripts: { record_max_bytes: 1024 } };
  const preview = await api.post(path + '/apply', { params: { dry_run: true }, data: document });
  expect(preview.status).toBe(200);
  expect(preview.body.outcomes.map((outcome: { op: string }) => outcome.op)).toEqual(['edit', 'edit']);
  expect((await api.get(path)).body.revision).toBe(revision);
  const rejected = await api.post(path + '/apply', { data: { ...document, annotation_interval_seconds: -1 } });
  expect(rejected.status).toBe(422);
  expect((await api.get(path)).body.revision).toBe(revision);
  expect((await api.get(path + '/export')).body).toEqual(baseline);
  const applied = await api.post(path + '/apply', { data: document });
  expect(applied.status).toBe(200);
  expect(applied.body.apply_id).toBeTruthy();
  const changes = await api.get(path + '/changes');
  expect(changes.body.changes).toHaveLength(history.length + 1);
  expect(changes.body.changes[0].door).toBe('apply');

  const exported = await cli(['hub', 'admin', 'settings', 'export']);
  expect(exported.code, exported.stderr).toBe(0);
  expect(parse(exported.stdout)).toEqual({ ...baseline, ...document });
  const file = testInfo.outputPath('settings.toml');
  fs.writeFileSync(file, exported.stdout);
  const reapplied = await cli(['hub', 'admin', 'settings', 'apply', file, '--json']);
  expect(reapplied.code, reapplied.stderr).toBe(0);
  expect(JSON.parse(reapplied.stdout).outcomes).toEqual(
    expect.arrayContaining([expect.objectContaining({ key: 'action_pass_seconds', op: 'unchanged' }), expect.objectContaining({ key: 'transcripts.record_max_bytes', op: 'unchanged' })]),
  );
  expect(JSON.parse(reapplied.stdout).outcomes.every((outcome: { op: string }) => outcome.op === 'unchanged')).toBe(true);
  expect((await api.get(path)).body.revision).toBe(revision + 1);
});

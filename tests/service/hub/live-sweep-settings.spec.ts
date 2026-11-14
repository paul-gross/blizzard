import { expect, test } from '../../harness/fixtures';

// A hub-wide edit needs an owned hub. The controlled clock and stepped sweep make the next pass explicit.
test.use({ isolation: 'fresh-hub', clockMode: 'controlled' });

test('a committed interval edit is retained when the next forge-status pass runs', async ({ api, clock, forge, issue, repository }) => {
  const source = await repository();
  expect((await api.patch(`/api/work-sources/${source.name}`, { data: { annotate: true } })).status).toBe(200);
  const filed = await issue('live interval sweep', { repository: source });
  expect((await api.post(`/api/chunks/${filed.chunk.chunk_id}/promote`)).status).toBe(202);
  await api.sweep('forge_status');
  const issuePath = `/repos/${source.owner}/${source.name}/issues/${filed.issue.number}`;
  expect((await forge.call('DELETE', `${issuePath}/labels/blizzard:ingested`)).status).toBe(200);

  const initial = await api.get('/api/admin/settings');
  expect(initial.status).toBe(200);
  const edited = await api.patch('/api/admin/settings', {
    headers: { 'If-Match': String(initial.body.revision) },
    data: { set: { annotation_interval_seconds: 1 } },
  });
  expect(edited.status, JSON.stringify(edited.body)).toBe(200);
  // Five new intervals have elapsed, but not five old 120-second intervals. An unchanged probe
  // therefore tests the edited full-pass floor, rather than a pass forced by changed work.
  await clock.advance(5);
  await api.sweep('forge_status');

  const read = await forge.call('GET', issuePath);
  expect(read.status).toBe(200);
  expect(read.body.labels.map((label: { name: string }) => label.name)).toContain('blizzard:ingested');
  const settings = await api.get('/api/admin/settings');
  expect(settings.body.settings).toEqual(expect.arrayContaining([
    expect.objectContaining({ key: 'annotation_interval_seconds', value: 1 }),
  ]));
});

test('settings audit follows the hosted controlled clock', async ({ api, clock }) => {
  const instant = await clock.advance(86400);
  const initial = await api.get('/api/admin/settings');
  const edited = await api.patch('/api/admin/settings', {
    headers: { 'If-Match': String(initial.body.revision) },
    data: { set: { action_pass_seconds: 5 } },
  });
  expect(edited.status).toBe(200);
  const history = await api.get('/api/admin/settings/changes');
  expect(history.status).toBe(200);
  const change = history.body.changes.find((entry: { revision: number }) => entry.revision === initial.body.revision + 1);
  expect(new Date(change.recorded_at).getTime()).toBe(instant.getTime());
});

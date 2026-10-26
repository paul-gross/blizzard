import { expect, test } from '../../harness/fixtures';

// A running hub reads its work source from the record its own API created: ingest reaches the mock forge through it, an
// edit reaches the next forge-status pass, and a renamed source keeps its refs. Each test edits the tenant's work
// source, and the first runs a forge-status pass over its tenant, so each owns its tenant.
test.use({ isolation: 'fresh-tenant' });

test('test_a_source_created_through_the_api_ingests_from_the_mock_forge_and_takes_edits_live', async ({
  api,
  forge,
  issue,
  repository,
}) => {
  const source = await repository();
  const { issue: filed, chunk } = await issue('read me through the record', { repository: source });
  const items = await api.get(`/api/chunks/${chunk.chunk_id}/work-items`);
  expect(items.status).toBe(200);
  expect(items.body.items.map((item: { title: string }) => item.title)).toEqual(['read me through the record']);
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);

  const edited = await api.patch(`/api/work-sources/${source.name}`, { data: { annotate: true } });
  expect(edited.status).toBe(200);

  // The pass after the edit is the next sweep pass.
  await api.sweep('forge_status');

  const read = await forge.call('GET', `/repos/${source.owner}/${source.name}/issues/${filed.number}`);
  expect(read.status).toBe(200);
  expect(read.body.labels.map((label: { name: string }) => label.name)).toContain('blizzard:ingested');
});

test('test_a_renamed_source_keeps_its_refs_and_renders_them_under_its_new_slug', async ({ api, issue, repository }) => {
  const source = await repository();
  const { issue: filed, chunk } = await issue('follow the rename', { repository: source });
  const chunkId = chunk.chunk_id;

  const renamed = await api.patch(`/api/work-sources/${source.name}`, { data: { slug: 'renamed', name: 'Renamed source' } });
  expect(renamed.status).toBe(200);
  const sourceId = renamed.body.work_source_id as string;

  const held = await api.get(`/api/chunks/${chunkId}`);
  expect(held.body.work_refs).toHaveLength(1);
  const [pointer] = held.body.work_refs;
  expect([pointer.source, pointer.label]).toEqual([sourceId, `renamed#${filed.number}`]);
  const items = await api.get(`/api/chunks/${chunkId}/work-items`);
  expect(items.body.items).toHaveLength(1);
  const [item] = items.body.items;
  expect([item.slug, item.title]).toEqual(['renamed', 'follow the rename']);

  for (const token of [`${source.name}:${filed.number}`, `renamed:${filed.number}`, `${sourceId}:${filed.number}`]) {
    const again = await api.post('/api/chunks', { data: { tokens: [token] } });
    expect(again.status, token).toBe(409);
    expect([again.body.existing_chunk_id, again.body.label]).toEqual([chunkId, `renamed#${filed.number}`]);
  }
  expect((await api.get(`/api/work-sources/${source.name}`)).body.work_source_id).toBe(sourceId);
});

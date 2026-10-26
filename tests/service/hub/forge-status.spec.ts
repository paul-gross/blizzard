import { expect, test } from '../../harness/fixtures';

// The hub's forge-status sweep against the real mock forge: an opted-in source's issues are labelled, and a source that
// has not opted in is never labelled. Each test configures its tenant's work source and runs a forge-status pass over
// its tenant, which on the common tenant would reach every test's issues, so each owns its tenant.
test.use({ isolation: 'fresh-tenant' });

test('test_an_opted_in_source_sweeps_the_real_forge', async ({ api, forge, issue, repository }) => {
  const source = await repository();
  // Opted in before the ingest, as the source was seeded.
  expect((await api.patch(`/api/work-sources/${source.name}`, { data: { annotate: true } })).status).toBe(200);
  const filed = await issue('sweep me', { repository: source });
  expect((await api.post(`/api/chunks/${filed.chunk.chunk_id}/promote`)).status).toBe(202);

  await api.sweep('forge_status');

  expect(await labelsOf(forge, source, filed.issue.number)).toContain('blizzard:ingested');
});

test('test_a_non_opted_in_source_starts_no_sweep_loop', async ({ api, forge, issue, repository }) => {
  const source = await repository();
  const filed = await issue("don't sweep me", { repository: source });
  expect((await api.post(`/api/chunks/${filed.chunk.chunk_id}/promote`)).status).toBe(202);

  await api.sweep('forge_status');

  // A source that has not opted in is never labelled.
  expect(await labelsOf(forge, source, filed.issue.number)).toEqual([]);
});

type Forge = Parameters<Parameters<typeof test>[2]>[0]['forge'];

async function labelsOf(forge: Forge, source: { owner: string; name: string }, number: number): Promise<string[]> {
  const read = await forge.call('GET', `/repos/${source.owner}/${source.name}/issues/${number}`);
  expect(read.status).toBe(200);
  return read.body.labels.map((label: { name: string }) => label.name);
}

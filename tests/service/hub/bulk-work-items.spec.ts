import { expect, test } from '../../harness/fixtures';

// The hub's bulk work-items read against the real mock forge: one GraphQL request answers the titles of every issue the
// chunks hold, and an issue the forge does not have errors only its own entry.
test.use({ isolation: 'fresh-tenant' });

interface Entry {
  title: string | null;
  error: string | null;
  comments: string[];
  web_url: string | null;
  label: string | null;
}

const bulkPath = (chunkIds: string[]) => `/api/work-items?${chunkIds.map((id) => `chunk_id=${encodeURIComponent(id)}`).join('&')}`;

test('test_a_bulk_read_resolves_titles_and_errors_a_missing_issue_alone', async ({ api, issue, repository }) => {
  const source = await repository();
  const first = await issue('first bulk title', { repository: source });
  const second = await issue('second bulk title', { repository: source });
  const missing = await api.post('/api/chunks', { data: { tokens: [`${source.tokenPrefix}:9999`] } });
  expect(missing.status).toBe(201);
  const missingId = missing.body.chunk_id as string;

  const bulk = await api.get(bulkPath([first.chunk.chunk_id, second.chunk.chunk_id, missingId]));

  expect(bulk.status).toBe(200);
  const chunks = bulk.body.chunks as Record<string, Entry[]>;
  const entryOf = (chunkId: string): Entry => {
    const entry = chunks[chunkId]?.[0];
    if (entry === undefined) throw new Error(`the bulk read omitted ${chunkId}`);
    return entry;
  };
  expect(entryOf(first.chunk.chunk_id).title).toBe('first bulk title');
  expect(entryOf(second.chunk.chunk_id).title).toBe('second bulk title');
  expect(entryOf(first.chunk.chunk_id).comments).toEqual([]);
  expect(entryOf(first.chunk.chunk_id).error).toBeNull();
  expect(entryOf(missingId).title).toBeNull();
  expect(entryOf(missingId).error).toBeTruthy();

  const single = await api.get(`/api/chunks/${first.chunk.chunk_id}/work-items`);
  expect(entryOf(first.chunk.chunk_id).web_url).toBe(single.body.items[0].web_url);
  expect(entryOf(first.chunk.chunk_id).label).toBe(single.body.items[0].label);
});

test('test_more_than_a_hundred_chunk_ids_is_refused', async ({ api }) => {
  const ids = Array.from({ length: 101 }, (_, i) => `ch_missing${i}`);
  expect((await api.get(bulkPath(ids))).status).toBe(422);
});

import { HubInternal } from '../client/internal';
import { expect, test } from '../fixtures';

test.use({ isolation: 'common' });

test('a list holds only the test’s own chunk, the whole list holds the neighbour’s, and an unnamed list comes back whole', async ({
  api,
  issue,
  hub: shared,
}) => {
  const hub = shared.coordinates();
  const mine = await issue('the test’s own');

  // A neighbour: an issue ingested through the internal client, which bypasses the ledger.
  const forge = new HubInternal(hub.forgeUrl);
  const filed = await forge.call<{ number: number }>('POST', '/repos/blizzard/common/issues', { body: { title: 'a neighbour’s' } });
  const neighbour = await new HubInternal(hub.url).call<{ chunk_id: string }>('POST', '/api/chunks', {
    tenant: hub.commonTenantId,
    body: { tokens: [`common:${filed.number}`], project: 'default' },
  });

  const narrowed = await api.get('/api/chunks');
  expect(narrowed.body.chunks.map((chunk: { chunk_id: string }) => chunk.chunk_id)).toEqual([mine.chunk.chunk_id]);

  const whole = await api.unfiltered.get('/api/chunks');
  const ids = whole.body.chunks.map((chunk: { chunk_id: string }) => chunk.chunk_id);
  expect(ids).toContain(mine.chunk.chunk_id);
  expect(ids).toContain(neighbour.chunk_id);

  // `resources.ts` does not name this list, so it comes back whole, holding the furnished repository.
  const links = await api.get('/api/projects/default/repository-links');
  expect(JSON.stringify(links.body)).toContain('common');
});

test('the ledger records what a create call returns, under its kind', async ({ api, issue, ledger }) => {
  const { chunk } = await issue('recorded');
  expect(ledger.ids('chunk')).toEqual([chunk.chunk_id]);
  expect((await api.get('/api/chunks')).body.chunks).toHaveLength(1);
});

import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { FLEET_CLOCK, hubBoardChunkWorkItemsKey, hubChunkWorkItemsKey, hubClient, type WorkItemsView } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient, workItemEntry } from 'fleet/testing';
import { WORK_ITEMS_BATCH_SIZE } from './board-work-items.model';
import { injectBoardWorkItemsQuery } from './board-work-items.query';

@Component({
  selector: 'app-test-board-work-items-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestHost {
  readonly chunkIds = signal<readonly string[]>([]);
  readonly workItems = injectBoardWorkItemsQuery(() => this.chunkIds());
}

const NOW = 10_000_000;
const ids = (n: number) => Array.from({ length: n }, (_, i) => `ch_${String(i).padStart(4, '0')}`);

/** The chunk ids a bulk `/api/work-items` request names. */
const idsOf = (search: string) => new URLSearchParams(search).getAll('chunk_id');

const titled = (chunkId: string): WorkItemsView => ({
  items: [
    workItemEntry({
      source: 'hub',
      ref: chunkId,
      title: chunkId,
      fetched_at: 't',
      label: null,
      body: null,
      web_url: null,
    }),
  ],
});

/** The bulk answer for the chunks a request names, omitting any in `omit`. */
const bulkAnswer = (search: string, omit: readonly string[] = []) => ({
  chunks: Object.fromEntries(
    idsOf(search)
      .filter((id) => !omit.includes(id))
      .map((id) => [id, titled(id).items]),
  ),
});

describe('injectBoardWorkItemsQuery', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;
  afterEach(() => stub?.restore());

  const mount = (seed?: (client: QueryClient) => void) => {
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    seed?.(queryClient);
    TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(queryClient),
        { provide: FLEET_CLOCK, useValue: () => NOW },
      ],
    });
    return TestBed.createComponent(TestHost);
  };

  const readAll = async (chunkIds: readonly string[], seed?: (client: QueryClient) => void) => {
    const fixture = mount(seed);
    fixture.componentInstance.chunkIds.set(chunkIds);
    await vi.waitFor(async () => {
      await settle(fixture, 1);
      expect(queryClient.isFetching()).toBe(0);
    });
    return fixture.componentInstance.workItems();
  };

  it('reads 20 chunks in one request', async () => {
    stub = stubRequestClient(hubClient, (_method, _path, search) => bulkAnswer(search));
    const all = ids(20);
    const found = await readAll(all);

    expect(found.size).toBe(20);
    expect(found.get('ch_0019')?.items?.[0].title).toBe('ch_0019');
    expect(stub.requests).toHaveLength(1);
    expect(stub.requests[0].path).toBe('/api/work-items');
    expect(idsOf(stub.requests[0].search)).toEqual(all);
  });

  it('answers every chunk past one batch with one request per batch', async () => {
    stub = stubRequestClient(hubClient, (_method, _path, search) => bulkAnswer(search));
    const all = ids(WORK_ITEMS_BATCH_SIZE + 5);
    const found = await readAll(all);

    expect(found.size).toBe(all.length);
    expect(found.get('ch_0104')?.items?.[0].title).toBe('ch_0104');
    expect(stub.requests).toHaveLength(2);
    expect(stub.requests.map((r) => idsOf(r.search).length).sort((a, b) => a - b)).toEqual([5, WORK_ITEMS_BATCH_SIZE]);
  });

  it('never writes the dock entry, holding what it read under its own key', async () => {
    stub = stubRequestClient(hubClient, (_method, _path, search) => bulkAnswer(search));
    await readAll(['ch_a']);

    expect(queryClient.getQueryData(hubChunkWorkItemsKey('ch_a'))).toBeUndefined();
    expect(queryClient.getQueryData(hubBoardChunkWorkItemsKey('ch_a'))).toEqual(titled('ch_a'));
  });

  it('reuses a chunk read within the hold even once a live update has invalidated it', async () => {
    stub = stubRequestClient(hubClient, (_method, _path, search) => bulkAnswer(search));
    const found = await readAll(['ch_held', 'ch_new'], (client) => {
      client.setQueryData(hubChunkWorkItemsKey('ch_held'), titled('ch_held'), {
        updatedAt: NOW - 60_000,
      });
      void client.invalidateQueries({
        queryKey: hubChunkWorkItemsKey('ch_held'),
      });
    });

    expect(stub.requests.flatMap((r) => idsOf(r.search))).toEqual(['ch_new']);
    expect(found.get('ch_held')).toEqual(titled('ch_held'));
  });

  it("reuses a chunk the board's own earlier read holds", async () => {
    stub = stubRequestClient(hubClient, (_method, _path, search) => bulkAnswer(search));
    await readAll(['ch_held'], (client) => {
      client.setQueryData(hubBoardChunkWorkItemsKey('ch_held'), titled('ch_held'), { updatedAt: NOW - 60_000 });
    });

    expect(stub.requests).toHaveLength(0);
  });

  it('re-reads a chunk whose last read is past the hold', async () => {
    stub = stubRequestClient(hubClient, (_method, _path, search) => bulkAnswer(search));
    await readAll(['ch_old'], (client) => {
      client.setQueryData(hubChunkWorkItemsKey('ch_old'), titled('ch_old'), {
        updatedAt: NOW - 6 * 60_000,
      });
    });

    expect(stub.requests.flatMap((r) => idsOf(r.search))).toEqual(['ch_old']);
  });

  it('answers a chunk the bulk answer omits as carrying no items, keeping the rest, and holds only the answered', async () => {
    stub = stubRequestClient(hubClient, (_method, _path, search) => bulkAnswer(search, ['ch_gone']));
    const found = await readAll(['ch_gone', 'ch_ok']);

    expect(found.get('ch_gone')).toEqual({ items: [] });
    expect(found.get('ch_ok')?.items?.length).toBeGreaterThan(0);
    expect(queryClient.getQueryData(hubBoardChunkWorkItemsKey('ch_gone'))).toBeUndefined();
  });

  it('answers every unheld chunk as carrying no items when the bulk call fails, holding none', async () => {
    stub = stubRequestClient(hubClient, () => stubError(502, { detail: 'hub down' }));
    const found = await readAll(['ch_a', 'ch_b']);

    expect(found.get('ch_a')).toEqual({ items: [] });
    expect(found.get('ch_b')).toEqual({ items: [] });
    expect(queryClient.getQueryData(hubBoardChunkWorkItemsKey('ch_a'))).toBeUndefined();
  });

  it("shows a batch's titles as soon as it answers, while a later batch still reads", async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    const all = ids(WORK_ITEMS_BATCH_SIZE + 1);
    const last = all[all.length - 1];
    stub = stubRequestClient(hubClient, async (_method, _path, search) => {
      if (idsOf(search).includes(last)) await held;
      return bulkAnswer(search);
    });
    const fixture = mount();
    fixture.componentInstance.chunkIds.set(all);

    await vi.waitFor(async () => {
      await settle(fixture, 1);
      expect(fixture.componentInstance.workItems().size).toBe(WORK_ITEMS_BATCH_SIZE);
    });
    expect(fixture.componentInstance.workItems().has(last)).toBe(false);

    release();
    await vi.waitFor(async () => {
      await settle(fixture, 1);
      expect(fixture.componentInstance.workItems().has(last)).toBe(true);
    });
  });
});

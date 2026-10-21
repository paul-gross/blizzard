import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { FLEET_CLOCK, hubChunkWorkItemsKey, hubClient, type WorkItemsView } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient, workItemEntry } from 'fleet/testing';
import { WORK_ITEMS_BATCH_SIZE } from './board-work-items.model';
import { injectBoardWorkItemsQuery, WORK_ITEMS_READ_CONCURRENCY } from './board-work-items.query';

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

/** The chunk id a `/api/chunks/{id}/work-items` path names. */
const chunkOf = (path: string) => path.split('/')[3];

const titled = (chunkId: string): WorkItemsView => ({
  items: [
    workItemEntry({ source: 'hub', ref: chunkId, title: chunkId, fetched_at: 't', label: null, body: null, web_url: null }),
  ],
});

describe('injectBoardWorkItemsQuery', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;
  afterEach(() => stub?.restore());

  const mount = (seed?: (client: QueryClient) => void) => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
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

  it('answers every chunk past one batch, keyed by chunk id, and fills the dock entry', async () => {
    stub = stubRequestClient(hubClient, (_method, path) => titled(chunkOf(path)));
    const all = ids(WORK_ITEMS_BATCH_SIZE + 5);
    const found = await readAll(all);

    expect(found.size).toBe(all.length);
    expect(found.get('ch_0104')?.items?.[0].title).toBe('ch_0104');
    expect(stub.requests).toHaveLength(all.length);
    expect(queryClient.getQueryData(hubChunkWorkItemsKey('ch_0000'))).toEqual(titled('ch_0000'));
  });

  it('reuses a chunk read within the hold even once a live update has invalidated it', async () => {
    stub = stubRequestClient(hubClient, (_method, path) => titled(chunkOf(path)));
    const found = await readAll(['ch_held', 'ch_new'], (client) => {
      client.setQueryData(hubChunkWorkItemsKey('ch_held'), titled('ch_held'), { updatedAt: NOW - 60_000 });
      void client.invalidateQueries({ queryKey: hubChunkWorkItemsKey('ch_held') });
    });

    expect(stub.requests.map((r) => chunkOf(r.path))).toEqual(['ch_new']);
    expect(found.get('ch_held')).toEqual(titled('ch_held'));
  });

  it('re-reads a chunk whose last read is past the hold', async () => {
    stub = stubRequestClient(hubClient, (_method, path) => titled(chunkOf(path)));
    await readAll(['ch_old'], (client) => {
      client.setQueryData(hubChunkWorkItemsKey('ch_old'), titled('ch_old'), { updatedAt: NOW - 6 * 60_000 });
    });

    expect(stub.requests.map((r) => chunkOf(r.path))).toEqual(['ch_old']);
  });

  it('answers a chunk whose read fails as carrying no items, keeping the rest', async () => {
    stub = stubRequestClient(hubClient, (_method, path) =>
      chunkOf(path) === 'ch_bad' ? stubError(502, { detail: 'forge down' }) : titled(chunkOf(path)),
    );
    const found = await readAll(['ch_bad', 'ch_ok']);

    expect(found.get('ch_bad')).toEqual({ items: [] });
    expect(found.get('ch_ok')?.items?.length).toBeGreaterThan(0);
  });

  it(`never has more than ${WORK_ITEMS_READ_CONCURRENCY} reads of a batch in flight`, async () => {
    let inFlight = 0;
    let peak = 0;
    stub = stubRequestClient(hubClient, async (_method, path) => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 0));
      inFlight -= 1;
      return titled(chunkOf(path));
    });
    await readAll(ids(20));

    expect(peak).toBe(WORK_ITEMS_READ_CONCURRENCY);
  });

  it("shows a batch's titles as soon as it answers, while a later batch still reads", async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    const all = ids(WORK_ITEMS_BATCH_SIZE + 1);
    const last = all[all.length - 1];
    stub = stubRequestClient(hubClient, async (_method, path) => {
      if (chunkOf(path) === last) await held;
      return titled(chunkOf(path));
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

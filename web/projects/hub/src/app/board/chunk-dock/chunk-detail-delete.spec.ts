import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { type ChunkDetail as ChunkDetailModel, hubChunkKey, hubChunkWorkItemsKey, hubClient } from 'fleet';
import { settle, OPERATOR_ME_RESPONSE, chunkDetail, type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { ChunkDetail } from './chunk-detail';

// An unacquired chunk the Delete verb reaches.
const DELETABLE_DETAIL: ChunkDetailModel = chunkDetail({
  graph_name: null,
  current_node_name: null,
  chunk_id: 'ch_deletable',
  graph_id: 'gr_default',
  status: 'not_ready',
  status_if_paused: 'paused',
  pausable: true,
  completable: true,
  deletable: true,
  graph_editable: true,
  current_node_id: null,
  latest_epoch: null,
  work_refs: [],
  history: [],
  artifacts: [],
});

async function confirmAction(fixture: ReturnType<typeof TestBed.createComponent<ChunkDetail>>): Promise<void> {
  await fixture.whenStable();
  (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')!.click();
  await fixture.whenStable();
}

async function clickMenuAction(fixture: ReturnType<typeof TestBed.createComponent<ChunkDetail>>, testid: string): Promise<void> {
  const el = fixture.nativeElement as HTMLElement;
  el.querySelector<HTMLButtonElement>('[data-testid="chunk-actions-menu"]')?.click();
  await fixture.whenStable();
  document.body.querySelector<HTMLButtonElement>(`[data-testid="${testid}"]`)?.click();
  await fixture.whenStable();
}

describe('ChunkDetail delete invalidation', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/chunks/ch_deletable') return DELETABLE_DETAIL;
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [ChunkDetail],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('dismisses as soon as delete succeeds, without waiting for invalidations to finish', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_deletable');
    let dismissed = false;
    fixture.componentInstance.dismiss.subscribe(() => (dismissed = true));
    await settle(fixture);

    let resolveInvalidate!: () => void;
    vi.spyOn(TestBed.inject(QueryClient), 'invalidateQueries').mockReturnValue(
      new Promise<void>((resolve) => (resolveInvalidate = resolve)),
    );
    await clickMenuAction(fixture, 'delete-chunk');
    await confirmAction(fixture);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(dismissed).toBe(true);
    resolveInvalidate();
    await settle(fixture);
  });

  it('marks deleted chunk queries stale without refetching them while the dock is still active', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_deletable');
    await settle(fixture);

    await clickMenuAction(fixture, 'delete-chunk');
    await confirmAction(fixture);
    await settle(fixture);

    expect(stub.forRoute('/api/chunks/ch_deletable', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/api/chunks/ch_deletable/work-items', 'GET')).toHaveLength(1);
    const client = TestBed.inject(QueryClient);
    expect(client.getQueryState(hubChunkKey('ch_deletable'))?.isInvalidated).toBe(true);
    expect(client.getQueryState(hubChunkWorkItemsKey('ch_deletable'))?.isInvalidated).toBe(true);
  });
});

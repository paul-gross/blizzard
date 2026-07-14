import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { settle } from '../testing/settle';
import { type HubClientStub, stubHubClient } from '../testing/stub-hub-client';
import { QueuePanel } from './queue-panel';

const QUEUE = {
  entries: [
    { chunk_id: 'ch_top', graph_id: 'gr_1', position: 0, pm_pointers: [{ provider: 'github', url: 'u/1' }] },
    { chunk_id: 'ch_mid', graph_id: 'gr_1', position: 1, pm_pointers: [{ provider: 'github', url: 'u/2' }] },
    { chunk_id: 'ch_low', graph_id: 'gr_1', position: 2, pm_pointers: [] },
  ],
};

describe('QueuePanel', () => {
  let stub: HubClientStub;

  beforeEach(async () => {
    stub = stubHubClient((method, path) => {
      if (method === 'GET' && path === '/api/queue/peek') return QUEUE;
      if (path === '/api/queue/reorder') return { entries: QUEUE.entries };
      if (path.endsWith('/group')) return { chunk_id: 'ch_mid', merged_chunk_ids: ['ch_low'] };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [QueuePanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('fires the reorder client call with position 0 on move-to-top (D-048)', async () => {
    const fixture = TestBed.createComponent(QueuePanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="queue-row"]');
    expect(rows).toHaveLength(3);

    // Move the middle chunk to the top.
    const midRow = el.querySelector('[data-chunk="ch_mid"]');
    midRow?.querySelector<HTMLButtonElement>('[data-testid="queue-move-top"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/queue/reorder', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ chunk_id: 'ch_mid', position: 0 });
  });

  it('groups multi-selected chunks into the top-most survivor (D-047)', async () => {
    const fixture = TestBed.createComponent(QueuePanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const check = (chunkId: string): void => {
      el.querySelector<HTMLInputElement>(`[data-chunk="${chunkId}"] [data-testid="queue-select"]`)?.click();
    };
    check('ch_mid');
    check('ch_low');
    fixture.detectChanges();

    el.querySelector<HTMLButtonElement>('[data-testid="group-selected"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/chunks/ch_mid/group', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ merge_chunk_ids: ['ch_low'] });
  });
});

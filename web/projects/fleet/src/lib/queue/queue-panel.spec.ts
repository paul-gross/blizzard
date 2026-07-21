import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { settle } from '../testing/settle';
import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { QueuePanel } from './queue-panel';

const QUEUE = {
  entries: [
    { chunk_id: 'ch_top', graph_id: 'gr_1', position: 0, pm_pointers: [{ source: 'widget', ref: '1' }] },
    { chunk_id: 'ch_mid', graph_id: 'gr_1', position: 1, pm_pointers: [{ source: 'widget', ref: '2' }] },
    { chunk_id: 'ch_low', graph_id: 'gr_1', position: 2, pm_pointers: [] },
  ],
};

describe('QueuePanel', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/queue') return QUEUE;
      if (method === 'PUT' && path === '/api/queue') return { entries: QUEUE.entries };
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

  it('fires the whole-order queue replace with the moved chunk at the front on move-to-top', async () => {
    const fixture = TestBed.createComponent(QueuePanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="queue-row"]');
    expect(rows).toHaveLength(3);

    // Move the middle chunk to the top.
    const midRow = el.querySelector('[data-chunk="ch_mid"]');
    midRow?.querySelector<HTMLButtonElement>('[data-testid="queue-move-top"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/queue', 'PUT');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ chunk_ids: ['ch_mid', 'ch_top', 'ch_low'] });
  });

  it('groups multi-selected chunks into the top-most survivor', async () => {
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

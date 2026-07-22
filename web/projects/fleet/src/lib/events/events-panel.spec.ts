import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { settle } from '../testing/settle';
import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { EventsPanel } from './events-panel';

const EVENTS = [
  {
    id: 2,
    recorded_at: '2026-07-16T00:00:02Z',
    severity: 'critical',
    kind: 'escalation-opened',
    runner_id: 'rn_02',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB',
    message: 'Runner escalated: build failed three times',
  },
  {
    id: 1,
    recorded_at: '2026-07-16T00:00:01Z',
    severity: 'info',
    kind: 'lease-minted',
    runner_id: 'rn_01',
    message: 'Lease minted',
  },
];

describe('EventsPanel', () => {
  let stub: RequestClientStub;

  const render = async (events: unknown = EVENTS) => {
    stub = stubRequestClient(hubClient, (method, path) => (method === 'GET' && path === '/api/events' ? { events } : {}));
    await TestBed.configureTestingModule({
      imports: [EventsPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(EventsPanel);
    await settle(fixture);
    return fixture;
  };

  afterEach(() => stub.restore());

  it('reads GET /api/events and renders the feed it gets back', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(stub.forRoute('/api/events', 'GET').length).toBeGreaterThan(0);
    expect(el.querySelectorAll('[data-testid="events-row"]')).toHaveLength(2);
    expect(el.querySelector('[data-testid="events-count"]')?.textContent).toContain('2');
  });

  it('rests on an empty state when the hub reports no events', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="events-empty"]')).not.toBeNull();
    expect(el.querySelectorAll('[data-testid="events-row"]')).toHaveLength(0);
  });

  it('emits the chunk id when a row is activated', async () => {
    const fixture = await render();
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="events-chunk"]')?.click();
    expect(selected).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YAB');
  });

  it('re-queries the hub when the severity filter changes', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    const before = stub.forRoute('/api/events', 'GET').length;

    el.querySelector<HTMLButtonElement>('[data-testid="events-filter-critical"]')?.click();
    await settle(fixture);

    const after = stub.forRoute('/api/events', 'GET').length;
    expect(after).toBeGreaterThan(before);
  });

  it('derives runner filter chips from the feed and re-queries when a runner is chosen', async () => {
    // The fixture carries two distinct runners (rn_01/rn_02), so the runner filter row shows.
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="events-runner-filter"]')).not.toBeNull();
    const before = stub.forRoute('/api/events', 'GET').length;

    el.querySelector<HTMLButtonElement>('[data-testid="events-runner-filter-rn_02"]')?.click();
    await settle(fixture);

    // The feed query (keyed on the runner filter) re-reads; the severity-only options
    // query does not, so the runner chips stay put.
    const after = stub.forRoute('/api/events', 'GET').length;
    expect(after).toBeGreaterThan(before);
    expect(el.querySelector('[data-testid="events-runner-filter-rn_02"]')).not.toBeNull();
  });

  it('derives chunk filter chips from the feed and re-queries when a chunk is chosen', async () => {
    // A feed spanning two distinct chunks (plus a runner-scoped, chunk-less event to prove
    // the null chunk_id is stripped from the universe rather than becoming an empty chip).
    const TWO_CHUNKS = [
      { id: 3, recorded_at: '2026-07-16T00:00:03Z', severity: 'critical', kind: 'worker-lost', runner_id: 'rn_01', chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB', message: 'lost a' },
      { id: 2, recorded_at: '2026-07-16T00:00:02Z', severity: 'warning', kind: 'attempt-failed', runner_id: 'rn_01', chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3ZZZ', message: 'retried b' },
      { id: 1, recorded_at: '2026-07-16T00:00:01Z', severity: 'info', kind: 'lease-minted', runner_id: 'rn_01', message: 'minted' },
    ];
    const fixture = await render(TWO_CHUNKS);
    const el = fixture.nativeElement as HTMLElement;
    // Two distinct chunks → the chunk filter row shows, one chip per chunk plus "All".
    expect(el.querySelector('[data-testid="events-chunk-filter"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="events-chunk-filter-ch_01KXKVVF1J3D6H6VYZ3XYN3YAB"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="events-chunk-filter-ch_01KXKVVF1J3D6H6VYZ3XYN3ZZZ"]')).not.toBeNull();
    const before = stub.forRoute('/api/events', 'GET').length;

    el.querySelector<HTMLButtonElement>('[data-testid="events-chunk-filter-ch_01KXKVVF1J3D6H6VYZ3XYN3ZZZ"]')?.click();
    await settle(fixture);

    const after = stub.forRoute('/api/events', 'GET').length;
    expect(after).toBeGreaterThan(before);
  });
});

import { provideZonelessChangeDetection } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { settle, type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { hubClient } from 'fleet';
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

/** The select's popup renders into a CDK overlay on `document.body`, not the fixture. */
const inOverlay = (testid: string) => document.body.querySelector<HTMLElement>(`[data-testid="${testid}"]`);

async function openSelect(fixture: ComponentFixture<unknown>, testid: string) {
  (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>(`[data-testid="${testid}"]`)?.click();
  await settle(fixture);
}

describe('EventsPanel', () => {
  let stub: RequestClientStub;

  const render = async (events: unknown = EVENTS, url = '/events') => {
    stub = stubRequestClient(hubClient, (method, path) => (method === 'GET' && path === '/api/events' ? { events } : {}));
    await TestBed.configureTestingModule({
      imports: [EventsPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([{ path: 'events', component: EventsPanel }]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    await TestBed.inject(Router).navigateByUrl(url);
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

  it('derives runner filter options from the feed and re-queries when a runner is chosen', async () => {
    // The fixture carries two distinct runners (rn_01/rn_02), so the runner filter row shows.
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="events-runner-filter"]')).not.toBeNull();
    const before = stub.forRoute('/api/events', 'GET').length;

    await openSelect(fixture, 'events-runner-filter');
    inOverlay('events-runner-filter-rn_02')?.click();
    await settle(fixture);

    // The feed query (keyed on the runner filter) re-reads; the severity-only options
    // query does not, so the runner options stay put.
    const after = stub.forRoute('/api/events', 'GET').length;
    expect(after).toBeGreaterThan(before);
    await openSelect(fixture, 'events-runner-filter');
    expect(inOverlay('events-runner-filter-rn_02')).not.toBeNull();
  });

  it('builds no blank runner option from an escalation row, which names no runner', async () => {
    // `GET /api/events` unions the event_log with a projection of every open escalation,
    // and a projected escalation carries `runner_id: null` — it must not become a
    // label-less chip whose value collides with the "All" reset sentinel.
    const WITH_ESCALATION = [
      { id: -1, recorded_at: '2026-07-16T00:00:03Z', severity: 'critical', kind: 'needs-human', runner_id: null, chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB', message: 'chunk needs a human' },
      ...EVENTS,
    ];
    const fixture = await render(WITH_ESCALATION);

    await openSelect(fixture, 'events-runner-filter');
    const chips = [...document.body.querySelectorAll('[role="listbox"] [role="option"]')];
    expect(chips.map((c) => c.textContent?.trim())).toEqual(['All', 'R-01', 'R-02']);
    // Only "All" reads as selected — no empty-valued chip shares its sentinel.
    expect(chips.filter((c) => c.getAttribute('aria-selected') === 'true')).toHaveLength(1);
    expect(inOverlay('events-runner-filter-all')?.getAttribute('aria-selected')).toBe('true');
  });

  it('builds no blank runner option from a real, hub-authored event_log row either', async () => {
    // Distinct from the escalation case above: this is a real `event_log` row (a positive
    // id, not a projection) that itself carries `runner_id: null` — a hub-authored event
    // names no runner (blizzard-context:/domain/operations.md). Same stripping rule, same
    // reset-sentinel hazard, a different source row.
    const WITH_HUB_AUTHORED = [
      { id: 3, recorded_at: '2026-07-16T00:00:03Z', severity: 'info', kind: 'work-item-closed', runner_id: null, chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB', message: 'closed' },
      ...EVENTS,
    ];
    const fixture = await render(WITH_HUB_AUTHORED);

    await openSelect(fixture, 'events-runner-filter');
    const chips = [...document.body.querySelectorAll('[role="listbox"] [role="option"]')];
    expect(chips.map((c) => c.textContent?.trim())).toEqual(['All', 'R-01', 'R-02']);
    expect(chips.filter((c) => c.getAttribute('aria-selected') === 'true')).toHaveLength(1);
    expect(inOverlay('events-runner-filter-all')?.getAttribute('aria-selected')).toBe('true');
  });

  it('derives chunk filter options from the feed and re-queries when a chunk is chosen', async () => {
    // A feed spanning two distinct chunks (plus a runner-scoped, chunk-less event to prove
    // the null chunk_id is stripped from the universe rather than becoming an empty chip).
    const TWO_CHUNKS = [
      { id: 3, recorded_at: '2026-07-16T00:00:03Z', severity: 'critical', kind: 'worker-lost', runner_id: 'rn_01', chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB', message: 'lost a' },
      { id: 2, recorded_at: '2026-07-16T00:00:02Z', severity: 'warning', kind: 'attempt-failed', runner_id: 'rn_01', chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3ZZZ', message: 'retried b' },
      { id: 1, recorded_at: '2026-07-16T00:00:01Z', severity: 'info', kind: 'lease-minted', runner_id: 'rn_01', message: 'minted' },
    ];
    const fixture = await render(TWO_CHUNKS);
    const el = fixture.nativeElement as HTMLElement;
    // Two distinct chunks → the chunk filter shows, one option per chunk plus "All".
    expect(el.querySelector('[data-testid="events-chunk-filter"]')).not.toBeNull();
    await openSelect(fixture, 'events-chunk-filter');
    expect(inOverlay('events-chunk-filter-ch_01KXKVVF1J3D6H6VYZ3XYN3YAB')).not.toBeNull();
    expect(inOverlay('events-chunk-filter-ch_01KXKVVF1J3D6H6VYZ3XYN3ZZZ')).not.toBeNull();
    const before = stub.forRoute('/api/events', 'GET').length;

    inOverlay('events-chunk-filter-ch_01KXKVVF1J3D6H6VYZ3XYN3ZZZ')?.click();
    await settle(fixture);

    const after = stub.forRoute('/api/events', 'GET').length;
    expect(after).toBeGreaterThan(before);
  });

  it('initialises the chunk filter from ?chunk= and sends it to the hub as chunk_id', async () => {
    const fixture = await render(EVENTS, '/events?chunk=ch_01KXKVVF1J3D6H6VYZ3XYN3YAB');

    const reads = stub.forRoute('/api/events', 'GET');
    expect(reads.some((r) => new URLSearchParams(r.search).get('chunk_id') === 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB')).toBe(true);
    await openSelect(fixture, 'events-chunk-filter');
    expect(inOverlay('events-chunk-filter-ch_01KXKVVF1J3D6H6VYZ3XYN3YAB')?.getAttribute('aria-selected')).toBe('true');
  });

  it('shows a ?chunk= id absent from the options feed as the active chip', async () => {
    const fixture = await render(EVENTS, '/events?chunk=ch_01KXKVVF1J3D6H6VYZ3XYNOLD0');

    await openSelect(fixture, 'events-chunk-filter');
    expect(inOverlay('events-chunk-filter-ch_01KXKVVF1J3D6H6VYZ3XYNOLD0')?.getAttribute('aria-selected')).toBe('true');
  });

  it('writes a chunk chip toggle to the URL, and drops ?chunk= when cleared', async () => {
    const fixture = await render(EVENTS, '/events?chunk=ch_01KXKVVF1J3D6H6VYZ3XYNOLD0');
    const router = TestBed.inject(Router);

    await openSelect(fixture, 'events-chunk-filter');
    inOverlay('events-chunk-filter-ch_01KXKVVF1J3D6H6VYZ3XYN3YAB')?.click();
    await settle(fixture);
    await fixture.whenStable();
    expect(router.url).toBe('/events?chunk=ch_01KXKVVF1J3D6H6VYZ3XYN3YAB');

    await openSelect(fixture, 'events-chunk-filter');
    inOverlay('events-chunk-filter-all')?.click();
    await settle(fixture);
    await fixture.whenStable();
    expect(router.url).toBe('/events');
  });
});

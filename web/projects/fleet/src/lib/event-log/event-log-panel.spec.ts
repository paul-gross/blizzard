import { type WritableSignal, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { type ActivityView } from '../api/hub';
import { client as hubClient } from '../api/hub/client.gen';
import { FleetLiveUpdates, type LoggedEvent } from '../sse/fleet-live';
import type { SseStatus } from '../sse/sse.service';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { EventLogPanel } from './event-log-panel';

describe('EventLogPanel', () => {
  let log: WritableSignal<readonly LoggedEvent[]>;
  let status: WritableSignal<SseStatus>;
  let authFailed: WritableSignal<boolean>;
  let stub: RequestClientStub;

  const render = async (activity: readonly ActivityView[] = []) => {
    log = signal<readonly LoggedEvent[]>([]);
    status = signal<SseStatus>('open');
    authFailed = signal(false);
    // A stub live-update spine exposing just what the panel reads.
    const fakeLive = {
      log: () => log(),
      status: () => status(),
      authFailed: () => authFailed(),
    } as unknown as FleetLiveUpdates;
    stub = stubRequestClient(hubClient, (method, path) => (method === 'GET' && path === '/api/activity' ? { activity } : {}));
    await TestBed.configureTestingModule({
      imports: [EventLogPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: FleetLiveUpdates, useValue: fakeLive },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(EventLogPanel);
    await settle(fixture);
    return fixture;
  };

  afterEach(() => stub?.restore());

  it('shows an empty state once the backfill read resolves with nothing and no live frame has arrived', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="event-log-empty"]')).toBeTruthy();
  });

  it('renders a loading state while the backfill read is still in flight, not empty (AC)', async () => {
    const fakeLive = {
      log: () => [] as readonly LoggedEvent[],
      status: () => 'open' as SseStatus,
      authFailed: () => false,
    } as unknown as FleetLiveUpdates;
    stub = stubRequestClient(hubClient, () => ({ activity: [] }));
    await TestBed.configureTestingModule({
      imports: [EventLogPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: FleetLiveUpdates, useValue: fakeLive },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(EventLogPanel);
    // A single, un-awaited detectChanges: the query has mounted but its microtask
    // fetch has not yet resolved, so this is the "first in-flight fetch" instant the
    // AC cares about — it must read as loading, never empty.
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="event-log-loading"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="event-log-empty"]')).toBeNull();

    await settle(fixture);
    expect(el.querySelector('[data-testid="event-log-loading"]')).toBeNull();
    expect(el.querySelector('[data-testid="event-log-empty"]')).toBeTruthy();
  });

  it('shows an error state on a terminal auth failure even though the backfill read succeeded', async () => {
    const fixture = await render([]);
    authFailed.set(true);
    status.set('closed');
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="event-log-error"]')).toBeTruthy();
  });

  it('renders the backfilled feed on load, before any live frame arrives', async () => {
    const fixture = await render([
      { type: 'chunk-changed', key: 'k-old', at: '2020-01-01T00:00:00Z', chunk_id: 'ch_old', status: 'ready' },
    ]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="event-log-row"]')).toHaveLength(1);
    expect(el.querySelector('[data-testid="event-log-message"]')?.textContent?.trim()).toBe('C-old → ready');
  });

  it('dedupes a backfilled row against a live frame naming the same key, preferring the live copy', async () => {
    const fixture = await render([
      { type: 'chunk-changed', key: 'k1', at: '2020-01-01T00:00:00Z', chunk_id: 'ch_alp', status: 'queued' },
    ]);
    log.set([{ seq: 1, type: 'chunk-changed', data: { chunk_id: 'ch_alp', status: 'running', key: 'k1' }, at: 5_000, key: 'k1' }]);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="event-log-row"]')).toHaveLength(1);
    expect(el.querySelector('[data-testid="event-log-message"]')?.textContent?.trim()).toBe('C-alp → running');
  });

  it('never collides two keyless live frames with each other (a hub older than Phase 2 stamps no key)', async () => {
    const fixture = await render([]);
    log.set([
      { seq: 1, type: 'chunk-changed', data: { chunk_id: 'ch_old', status: 'ready' }, at: 1_000 },
      { seq: 2, type: 'chunk-changed', data: { chunk_id: 'ch_new', status: 'running' }, at: 5_000 },
    ]);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="event-log-row"]')).toHaveLength(2);
  });

  it('orders the merged feed newest-first across backfill and live', async () => {
    const fixture = await render([
      { type: 'chunk-changed', key: 'k-old', at: '2020-01-01T00:00:00Z', chunk_id: 'ch_old', status: 'ready' },
    ]);
    // A live frame's `at` is a ms-epoch instant (`Date.now()` at record time), so it must
    // be a realistic, recent instant to sort after the 2020 backfill row — not a small
    // offset-from-epoch number, which reads as 1970 and would sort *before* it.
    log.set([
      {
        seq: 1,
        type: 'chunk-changed',
        data: { chunk_id: 'ch_new', status: 'running', key: 'k-new' },
        at: Date.parse('2026-07-20T00:00:00Z'),
        key: 'k-new',
      },
    ]);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    const messages = [...el.querySelectorAll('[data-testid="event-log-message"]')].map((n) => n.textContent?.trim());
    expect(messages).toEqual(['C-new → running', 'C-old → ready']);
  });

  it('renders a backfilled row missing status with no placeholder dash', async () => {
    const fixture = await render([
      { type: 'chunk-changed', key: 'k1', at: '2020-01-01T00:00:00Z', chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN1RJ1', prev_node: 'review', node: 'build' },
    ]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="event-log-message"]')?.textContent?.trim()).toBe('C-1RJ1 review → build');
  });

  it('renders a runner-changed frame from the live tee as what actually changed', async () => {
    const fixture = await render([]);
    log.set([{ seq: 1, type: 'runner-changed', data: { runner_id: 'runner-local', kind: 'paused', by: 'operator' }, at: 0 }]);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="event-log-message"]')?.textContent?.trim()).toBe('runner runner-local paused by operator');
  });
});

import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { EVENT_SOURCE_FACTORY, ViewportService, type EventSourceFactory, type FleetEventSource } from 'fleet';
import { settle } from 'fleet/testing';

import { App } from './app';
import { routes } from './app.routes';

/** A do-nothing EventSource so `RunnerLiveUpdates` (blizzard#317 Phase 4) can open
 * without a real stream — mirrors the hub app-root's own `FakeEventSource`. */
class FakeEventSource {
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  addEventListener(): void {
    /* no-op: the test never drives the stream */
  }
  close(): void {
    /* no-op */
  }
}

describe('runner App', () => {
  const previousFetch = globalThis.fetch;

  beforeEach(async () => {
    // The shell mounts `LocalPanel`, which now polls `GET /api/leases` (issue #28) —
    // stub a minimal empty response so this shell-level test stays independent of
    // the local panel's own query behavior (covered by `local-panel`'s own specs).
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch;
    const factory: EventSourceFactory = () => new FakeEventSource() as unknown as FleetEventSource;
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        // `LocalPanel` now binds its selection to the URL's query params (issue #99),
        // so it injects the router — the shell test wires the real route table.
        provideRouter(routes),
        // `App` now starts `RunnerLiveUpdates` unconditionally (blizzard#317 Phase 4) —
        // a fake transport so the stream opens without ever reaching real `fetch`.
        { provide: EVENT_SOURCE_FACTORY, useValue: factory },
      ],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = previousFetch;
  });

  it('redirects the empty path to /board and renders the local-panel shell through the routed tab strip (issue #313)', async () => {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(router.url).toBe('/board');
    expect(el.querySelector('[data-testid="app-nav"]')).toBeTruthy();
    expect(el.querySelector('local-panel')).toBeTruthy();
    expect(el.querySelector('[data-testid="local-panel"]')).toBeTruthy();
  });

  it('resolves a selection query-param URL through the redirect and still mounts the panel', async () => {
    // The panel's selection rides in the URL's query params (issue #99). The
    // `''` redirect (`app.routes.ts`) must carry it through to `/board` — a
    // deep-linked reload lands here. `navigateByUrl` resolves `true` only on a
    // successful match, so this proves the route table itself, not just that
    // the panel renders.
    const router = TestBed.inject(Router);
    const fixture = TestBed.createComponent(App);

    const resolved = await router.navigateByUrl('/?chunk=ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9&attempt=lease_01KXKVVF1J3D6H6VYZ3XYNZPRR');
    await settle(fixture);

    expect(resolved).toBe(true);
    expect(router.url).toBe('/board?chunk=ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9&attempt=lease_01KXKVVF1J3D6H6VYZ3XYNZPRR');
    expect((fixture.nativeElement as HTMLElement).querySelector('local-panel')).toBeTruthy();
  });

  it('resolves /events to the full-width fact log, without mounting the board first', async () => {
    const router = TestBed.inject(Router);
    const fixture = TestBed.createComponent(App);
    const resolved = await router.navigateByUrl('/events');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(resolved).toBe(true);
    expect(el.querySelector('[data-testid="fact-log"]')).toBeTruthy();
    expect(el.querySelector('local-panel')).toBeNull();
  });

  it('marks the active route on the nav tabs and preserves the selection across a Board → Events → Board round trip', async () => {
    const router = TestBed.inject(Router);
    const fixture = TestBed.createComponent(App);
    await router.navigateByUrl('/board?chunk=ch_1');
    await settle(fixture);
    let el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="nav-board"]')?.classList.contains('active')).toBe(true);
    expect(el.querySelector('[data-testid="nav-events"]')?.classList.contains('active')).toBe(false);

    el.querySelector<HTMLElement>('[data-testid="nav-events"]')?.click();
    await settle(fixture);
    el = fixture.nativeElement as HTMLElement;

    expect(router.url).toBe('/events?chunk=ch_1');
    expect(el.querySelector('[data-testid="nav-events"]')?.classList.contains('active')).toBe(true);

    el.querySelector<HTMLElement>('[data-testid="nav-board"]')?.click();
    await settle(fixture);

    expect(router.url).toBe('/board?chunk=ch_1');
  });

  describe('mobile mode', () => {
    it('renders the persistent bottom tab bar (Board/Events routed, Asks/Transcripts inert) instead of the top nav', async () => {
      const viewport = TestBed.inject(ViewportService);
      viewport.setOverride('mobile');
      const router = TestBed.inject(Router);
      const fixture = TestBed.createComponent(App);
      await router.navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="app-nav"]')).toBeNull();
      expect(el.querySelector('[data-testid="local-panel-mobile-tab-bar"]')).toBeTruthy();
      const board = el.querySelector('[data-testid="tab-board"]');
      const events = el.querySelector('[data-testid="tab-events"]');
      const asks = el.querySelector('[data-testid="tab-asks-runner"]');
      const transcripts = el.querySelector('[data-testid="tab-transcripts-runner"]');
      expect(board?.classList.contains('on')).toBe(true);
      expect(events).toBeTruthy();
      expect(asks?.hasAttribute('disabled')).toBe(true);
      expect(transcripts?.hasAttribute('disabled')).toBe(true);
    });
  });
});

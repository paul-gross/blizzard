import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { EVENT_SOURCE_FACTORY, ViewportService, type EventSourceFactory, type FleetEventSource } from 'fleet';
import { settle } from 'fleet/testing';

import { App } from './app';
import { routes } from './app.routes';
import { ACTIVE_RUNNER } from '../core/active-runner';

/** A do-nothing EventSource so `RunnerLiveUpdates` can open
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
    // The shell mounts `LocalPanel`, which polls its runner's leases —
    // stub a minimal empty response so this shell-level test stays independent of
    // the local panel's own query behavior (covered by the board panel's own specs).
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
        // `LocalPanel` binds its selection to the URL's query params,
        // so it needs the router — the shell test wires the real route table.
        provideRouter(routes),
        // `App` starts `RunnerLiveUpdates` unconditionally —
        // a fake transport so the stream opens without ever reaching real `fetch`.
        { provide: EVENT_SOURCE_FACTORY, useValue: factory },
      ],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = previousFetch;
  });

  it('renders the host page at the root, with only the Host tab and no runner active', async () => {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(router.url).toBe('/');
    expect(el.querySelector('[data-testid="host-page"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="nav-host"]')?.classList.contains('active')).toBe(true);
    expect(el.querySelector('[data-testid="nav-board"]')).toBeNull();
    expect(el.querySelector('[data-testid="runner-switcher"]')).toBeTruthy();
    expect(TestBed.inject(ACTIVE_RUNNER)()).toBeNull();
  });

  it('mounts a runner’s board under /runners/:runnerId with its selection, and makes it the active runner', async () => {
    const router = TestBed.inject(Router);
    const fixture = TestBed.createComponent(App);

    const resolved = await router.navigateByUrl(
      '/runners/rn_1/board?chunk=ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9&attempt=lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
    );
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(resolved).toBe(true);
    expect(el.querySelector('app-panel')).toBeTruthy();
    expect(el.querySelector('[data-testid="local-panel"]')).toBeTruthy();
    expect(TestBed.inject(ACTIVE_RUNNER)()).toBe('rn_1');
  });

  it('follows the route to another runner', async () => {
    const router = TestBed.inject(Router);
    const fixture = TestBed.createComponent(App);
    await router.navigateByUrl('/runners/rn_1/board');
    await settle(fixture);
    await router.navigateByUrl('/runners/rn_2/events');
    await settle(fixture);

    expect(TestBed.inject(ACTIVE_RUNNER)()).toBe('rn_2');
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="nav-board"]')?.getAttribute('href')).toBe(
      '/runners/rn_2/board',
    );
  });

  it('resolves a runner’s events page to the full-width fact log, without mounting the board first', async () => {
    const router = TestBed.inject(Router);
    const fixture = TestBed.createComponent(App);
    const resolved = await router.navigateByUrl('/runners/rn_1/events');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(resolved).toBe(true);
    expect(el.querySelector('[data-testid="fact-log"]')).toBeTruthy();
    expect(el.querySelector('app-panel')).toBeNull();
  });

  it('resolves a runner’s machine page', async () => {
    const router = TestBed.inject(Router);
    const fixture = TestBed.createComponent(App);
    await router.navigateByUrl('/runners/rn_1/machine');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="machine-page"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="nav-machine"]')?.classList.contains('active')).toBe(true);
  });

  it('renders no board at the old root /board, and does not redirect', async () => {
    const router = TestBed.inject(Router);
    const fixture = TestBed.createComponent(App);
    await router.navigateByUrl('/board');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(router.url).toBe('/board');
    expect(el.querySelector('app-panel')).toBeNull();
    expect(el.querySelector('[data-testid="host-page"]')).toBeTruthy();
  });

  describe('the app-root header/nav/content order', () => {
    // Pins the header as app-root chrome via `AppShell`: it precedes the nav
    // strip in DOM order on a runner's board and stays mounted on its events, matching the
    // hub.
    it('renders the header above the tab strip on a board, not the other way around', async () => {
      const router = TestBed.inject(Router);
      const fixture = TestBed.createComponent(App);
      await router.navigateByUrl('/runners/rn_1/board');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      const header = el.querySelector('[data-testid="board-header"]');
      const nav = el.querySelector('[data-testid="app-nav"]');
      expect(header).toBeTruthy();
      expect(nav).toBeTruthy();
      expect(
        Boolean(header!.compareDocumentPosition(nav!) & Node.DOCUMENT_POSITION_FOLLOWING),
      ).toBe(true);
    });

    it('keeps the header mounted on a runner’s events, outside the board route', async () => {
      const router = TestBed.inject(Router);
      const fixture = TestBed.createComponent(App);
      await router.navigateByUrl('/runners/rn_1/events');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="board-header"]')).toBeTruthy();
    });
  });

  it('marks the active route on the nav tabs and preserves the selection across a Board → Events → Board round trip', async () => {
    const router = TestBed.inject(Router);
    const fixture = TestBed.createComponent(App);
    await router.navigateByUrl('/runners/rn_1/board?chunk=ch_1');
    await settle(fixture);
    let el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="nav-board"]')?.classList.contains('active')).toBe(true);
    expect(el.querySelector('[data-testid="nav-events"]')?.classList.contains('active')).toBe(false);
    expect(el.querySelector('[data-testid="nav-host"]')?.classList.contains('active')).toBe(false);

    el.querySelector<HTMLElement>('[data-testid="nav-events"]')?.click();
    await settle(fixture);
    el = fixture.nativeElement as HTMLElement;

    expect(router.url).toBe('/runners/rn_1/events?chunk=ch_1');
    expect(el.querySelector('[data-testid="nav-events"]')?.classList.contains('active')).toBe(true);

    el.querySelector<HTMLElement>('[data-testid="nav-board"]')?.click();
    await settle(fixture);

    expect(router.url).toBe('/runners/rn_1/board?chunk=ch_1');
  });

  describe('mobile mode', () => {
    it('renders the persistent bottom tab bar and the runner switcher instead of the top nav', async () => {
      const viewport = TestBed.inject(ViewportService);
      viewport.setOverride('mobile');
      const router = TestBed.inject(Router);
      const fixture = TestBed.createComponent(App);
      await router.navigateByUrl('/runners/rn_1/board');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="app-nav"]')).toBeNull();
      expect(el.querySelector('[data-testid="local-panel-mobile-tab-bar"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="runner-switcher"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="tab-board"]')?.classList.contains('on')).toBe(true);
      expect(el.querySelector('[data-testid="tab-host"]')?.classList.contains('on')).toBe(false);
      expect(el.querySelector('[data-testid="tab-events"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="tab-asks-runner"]')?.hasAttribute('disabled')).toBe(true);
    });
  });
});

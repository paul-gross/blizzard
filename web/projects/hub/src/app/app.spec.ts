import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { EVENT_SOURCE_FACTORY, FleetLiveUpdates, type EventSourceFactory } from 'fleet';
import { vi } from 'vitest';

import { App } from './app';
import { routes } from './app.routes';
import { BoardPage } from './board/board-page';

/** A do-nothing EventSource so the live-update spine can open without a real stream. */
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

describe('hub App', () => {
  let queryClient: QueryClient;

  beforeEach(async () => {
    const factory: EventSourceFactory = () => new FakeEventSource() as unknown as EventSource;
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(queryClient),
        provideRouter(routes),
        { provide: EVENT_SOURCE_FACTORY, useValue: factory },
      ],
    }).compileComponents();
  });

  it('renders the titlebar and nav, and redirects the empty path to /board', async () => {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board-header"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="app-nav"]')).toBeTruthy();

    expect(router.url).toBe('/board');
    expect(el.querySelector('[data-testid="board-shell"]')).toBeTruthy();
  });

  it('resolves the /graphs route to the graph explorer', async () => {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await fixture.whenStable();

    await router.navigateByUrl('/graphs');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-explorer"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="board-shell"]')).toBeNull();
  });

  it('marks the active route on the nav tabs (routerLinkActive)', async () => {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const boardTab = () => el.querySelector('[data-testid="nav-board"]');
    const graphsTab = () => el.querySelector('[data-testid="nav-graphs"]');

    expect(boardTab()?.classList.contains('active')).toBe(true);
    expect(graphsTab()?.classList.contains('active')).toBe(false);

    await router.navigateByUrl('/graphs');
    await fixture.whenStable();

    expect(boardTab()?.classList.contains('active')).toBe(false);
    expect(graphsTab()?.classList.contains('active')).toBe(true);
  });

  it('starts FleetLiveUpdates once from the app root and does not restart it on navigation', async () => {
    // Spy before the component is created: App's constructor calls `start()`
    // immediately, so the spy must be attached to the singleton beforehand.
    const live = TestBed.inject(FleetLiveUpdates);
    const startSpy = vi.spyOn(live, 'start');

    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await fixture.whenStable();

    expect(startSpy).toHaveBeenCalledTimes(1);

    await router.navigateByUrl('/graphs');
    await fixture.whenStable();
    await router.navigateByUrl('/board');
    await fixture.whenStable();

    expect(startSpy).toHaveBeenCalledTimes(1);
  });

  it('keeps the same QueryClient instance across navigation (query cache survives tab switches)', async () => {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await fixture.whenStable();

    // Resolve through the routed component's own injector, not TestBed's — a
    // regression where a routed component re-provides QueryClient would still
    // leave TestBed.inject(QueryClient) resolving the root singleton.
    const boardBefore = fixture.debugElement.query(By.directive(BoardPage));
    expect(boardBefore.injector.get(QueryClient)).toBe(queryClient);

    await router.navigateByUrl('/graphs');
    await fixture.whenStable();
    await router.navigateByUrl('/board');
    await fixture.whenStable();

    const boardAfter = fixture.debugElement.query(By.directive(BoardPage));
    expect(boardAfter.injector.get(QueryClient)).toBe(queryClient);
  });
});

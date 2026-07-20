import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter, withRouterConfig } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { EVENT_SOURCE_FACTORY, ViewportService, provideViewportRenavigation, type EventSourceFactory } from 'fleet';
import { settle } from 'fleet/testing';

import { App } from './app';
import { routes } from './app.routes';

/** A do-nothing EventSource so the live-update spine can open without a real stream
 * (`App`'s constructor calls `FleetLiveUpdates.start()` unconditionally). */
class FakeEventSource {
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  addEventListener(): void {
    /* no-op: no test here drives the stream */
  }
  close(): void {
    /* no-op */
  }
}

/**
 * The route table's same-URL, two-shell fork (`app.routes.ts`): `/board` mounts
 * the mobile glance shell or the desktop `BoardPage` depending on
 * `ViewportService.mode`, decided by `matchesMobileViewport` — never the page
 * component itself. Exercised through the real router (`App`'s `<router-outlet>`),
 * not by rendering either page component directly, so a regression that breaks the
 * guard wiring itself (not just one page's own template) is caught here.
 */
describe('the board route (route-table mobile/desktop fork)', () => {
  beforeEach(async () => {
    // ViewportService's override persists to localStorage — cleared so a prior
    // test's override never bleeds into the next one.
    localStorage.clear();
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes, withRouterConfig({ onSameUrlNavigation: 'reload' })),
        provideViewportRenavigation(),
        { provide: EVENT_SOURCE_FACTORY, useValue: (() => new FakeEventSource() as unknown as EventSource) as EventSourceFactory },
      ],
    }).compileComponents();
  });

  it('renders the mobile glance shell at /board when forced to mobile', async () => {
    TestBed.inject(ViewportService).setOverride('mobile');
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/board');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="glance-board"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="board-shell"]')).toBeNull();
  });

  it('renders the desktop BoardPage at /board when forced to desktop', async () => {
    TestBed.inject(ViewportService).setOverride('desktop');
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/board');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board-shell"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="glance-board"]')).toBeNull();
  });

  it('re-navigates and swaps shells live when the mode flips, with no URL change', async () => {
    const viewport = TestBed.inject(ViewportService);
    viewport.setOverride('desktop');
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/board');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board-shell"]')).toBeTruthy();

    viewport.setOverride('mobile');
    await settle(fixture);

    expect(router.url).toBe('/board');
    expect(el.querySelector('[data-testid="board-shell"]')).toBeNull();
    expect(el.querySelector('[data-testid="glance-board"]')).toBeTruthy();

    viewport.setOverride('desktop');
    await settle(fixture);

    expect(router.url).toBe('/board');
    expect(el.querySelector('[data-testid="glance-board"]')).toBeNull();
    expect(el.querySelector('[data-testid="board-shell"]')).toBeTruthy();
  });
});

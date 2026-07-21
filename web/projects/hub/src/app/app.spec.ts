import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  EVENT_SOURCE_FACTORY,
  FleetLiveUpdates,
  ViewportService,
  hubClient,
  provideAuthInterceptor,
  type EventSourceFactory,
  type FleetEventSource,
  type MeResponse,
  type ProviderSummary,
} from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
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

/** Stubs `/api/me` and `/api/auth/providers` — the two reads the app root's session
 * gate (`authState`, issue #93) depends on — plus every other route the app touches
 * (health/chunks/fleet-spend/queue/etc, all default to `{}` via `stubRequestClient`).
 * `me` defaults to the full-permission operator identity (`auth.mode = "none"`'s
 * shape), so a spec that does not care about auth exercises the app exactly as it
 * did before #93; a spec that does care overrides one or both. */
function stubAuth(me: MeResponse | null = OPERATOR_ME_RESPONSE, providers: readonly ProviderSummary[] = []): RequestClientStub {
  return stubRequestClient(hubClient, (method, path) => {
    if (path === '/api/me') return me === null ? stubError(401, { detail: 'not authenticated' }) : me;
    if (path === '/api/auth/providers') return providers;
    // The board/rail/graphs reads unwrap their response as a raw array
    // (`data ?? []`) — `{}` would resolve truthy-but-not-iterable and crash the
    // header/board/explorer's own `for…of`, so these need their real empty shape.
    if (path === '/api/chunks' || path === '/api/questions' || path === '/api/graphs') return [];
    // `BoardHeader` renders its spend cell whenever the read resolves truthy at all
    // (`{}` included) and calls `.toFixed` on `cost_usd` unconditionally.
    if (path === '/api/spend') return { cost_usd: 0, cost_partial: false };
    return {};
  });
}

describe('hub App', () => {
  let queryClient: QueryClient;
  let authStub: RequestClientStub;

  beforeEach(async () => {
    const factory: EventSourceFactory = () => new FakeEventSource() as unknown as FleetEventSource;
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    authStub = stubAuth();
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(queryClient),
        provideRouter(routes),
        provideAuthInterceptor(),
        { provide: EVENT_SOURCE_FACTORY, useValue: factory },
      ],
    }).compileComponents();
  });

  afterEach(() => authStub.restore());

  it('renders the titlebar and nav, and redirects the empty path to /board', async () => {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await settle(fixture);
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
    await settle(fixture);

    await router.navigateByUrl('/graphs');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-explorer"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="board-shell"]')).toBeNull();
  });

  it('marks the active route on the nav tabs (routerLinkActive)', async () => {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const boardTab = () => el.querySelector('[data-testid="nav-board"]');
    const graphsTab = () => el.querySelector('[data-testid="nav-graphs"]');

    expect(boardTab()?.classList.contains('active')).toBe(true);
    expect(graphsTab()?.classList.contains('active')).toBe(false);

    await router.navigateByUrl('/graphs');
    await settle(fixture);

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
    await settle(fixture);

    expect(startSpy).toHaveBeenCalledTimes(1);

    await router.navigateByUrl('/graphs');
    await settle(fixture);
    await router.navigateByUrl('/board');
    await settle(fixture);

    expect(startSpy).toHaveBeenCalledTimes(1);
  });

  it('keeps the same QueryClient instance across navigation (query cache survives tab switches)', async () => {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await settle(fixture);

    // Resolve through the routed component's own injector, not TestBed's — a
    // regression where a routed component re-provides QueryClient would still
    // leave TestBed.inject(QueryClient) resolving the root singleton.
    const boardBefore = fixture.debugElement.query(By.directive(BoardPage));
    expect(boardBefore.injector.get(QueryClient)).toBe(queryClient);

    await router.navigateByUrl('/graphs');
    await settle(fixture);
    await router.navigateByUrl('/board');
    await settle(fixture);

    const boardAfter = fixture.debugElement.query(By.directive(BoardPage));
    expect(boardAfter.injector.get(QueryClient)).toBe(queryClient);
  });

  describe('the mobile shell fork (ViewportService)', () => {
    afterEach(() => localStorage.removeItem('blizzard.viewport.override'));

    it('desktop mode renders the board-header/nav pair and no mobile chrome', async () => {
      TestBed.inject(ViewportService).setOverride('desktop');
      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="board-header"]')).not.toBeNull();
      expect(el.querySelector('[data-testid="app-nav"]')).not.toBeNull();
      expect(el.querySelector('[data-testid="mobile-titlebar"]')).toBeNull();
      expect(el.querySelector('[data-testid="mobile-tab-bar"]')).toBeNull();
    });

    it('mobile mode renders the mobile titlebar and the persistent tab bar instead', async () => {
      TestBed.inject(ViewportService).setOverride('mobile');
      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="mobile-titlebar"]')).not.toBeNull();
      expect(el.querySelector('[data-testid="mobile-tab-bar"]')).not.toBeNull();
      expect(el.querySelector('[data-testid="board-header"]')).toBeNull();
      expect(el.querySelector('[data-testid="app-nav"]')).toBeNull();
    });
  });

  describe('session-aware UI (issue #93)', () => {
    it('routes an unauthenticated session to /login, with no board chrome', async () => {
      authStub.restore();
      authStub = stubAuth(null, [{ name: 'oidc-co', display_name: 'Stub SSO', type: 'oidc' }]);

      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(router.url).toBe('/login');
      expect(el.querySelector('[data-testid="login-page"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="board-header"]')).toBeNull();
      expect(el.querySelector('[data-testid="app-nav"]')).toBeNull();
    });

    it('renders the guest lobby for an authenticated, permissionless identity', async () => {
      authStub.restore();
      const guest: MeResponse = {
        user_id: 'usr_1',
        username: 'newcomer',
        display_name: 'Newcomer',
        role: 'guest',
        permissions: [],
      };
      authStub = stubAuth(guest, [{ name: 'oidc-co', display_name: 'Stub SSO', type: 'oidc' }]);

      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="guest-lobby"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="board-header"]')).toBeNull();
      expect(el.querySelector('[data-testid="guest-lobby-username"]')?.textContent).toContain('newcomer');
    });

    it('logs out from the guest lobby and lands back on /login', async () => {
      authStub.restore();
      const guest: MeResponse = {
        user_id: 'usr_1',
        username: 'newcomer',
        display_name: 'Newcomer',
        role: 'guest',
        permissions: [],
      };
      let loggedOut = false;
      authStub = stubRequestClient(hubClient, (method, path) => {
        if (path === '/api/me') return loggedOut ? stubError(401, { detail: 'not authenticated' }) : guest;
        if (path === '/api/auth/providers') return [];
        if (path === '/api/auth/logout' && method === 'POST') {
          loggedOut = true;
          return {};
        }
        // Once logged out, `authState` briefly still shows whatever route was last
        // active while the redirect to /login lands — same empty-shape need as
        // `stubAuth` above, so that transient render does not crash.
        if (path === '/api/chunks' || path === '/api/questions' || path === '/api/graphs') return [];
        return {};
      });

      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="guest-lobby-logout"]')?.click();
      await settle(fixture);

      expect(router.url).toBe('/login');
    });

    it('shows the Admin nav tab only with user:manage under oauth, and hides it under none', async () => {
      authStub.restore();
      authStub = stubAuth(OPERATOR_ME_RESPONSE, [{ name: 'oidc-co', display_name: 'Stub SSO', type: 'oidc' }]);

      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      // Providers configured (oauth-shaped) + user:manage → the nav entry renders.
      expect(el.querySelector('[data-testid="nav-admin"]')).toBeTruthy();
    });

    it('hides the Admin nav tab under auth.mode=none even though the operator holds user:manage', async () => {
      // The default beforeEach stub: OPERATOR_ME_RESPONSE (holds user:manage) with an
      // empty provider list — the `none`-mode shape.
      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="nav-admin"]')).toBeNull();
      expect(el.querySelector('[data-testid="nav-logout"]')).toBeTruthy();
    });
  });
});

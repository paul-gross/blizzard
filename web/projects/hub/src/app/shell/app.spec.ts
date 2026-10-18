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
  type EventSourceFactory,
  type FleetEventSource,
  type MeResponse,
  type ProviderSummary,
} from 'fleet';
import { provideAuthInterceptor } from '../core/auth/auth.interceptor';
import { LAST_TENANT_KEY, TAB_TENANT_KEY, TenantContext } from '../core/auth/tenant-context';
import { provideTenantInterceptor } from '../core/auth/tenant.interceptor';
import type { MeTenant } from 'fleet/shell';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';
import { FLEET_CLOCK } from 'fleet';

import { App } from './app';
import { routes } from './app.routes';
import { BoardPage } from '../board/board-page';
import { startOfLocalDayIso, startOfPreviousLocalDayIso } from '../core/local-day';

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
 * gate (`authState`) depends on — plus every other route the app touches
 * (health/chunks/fleet-spend/queue/etc, all default to `{}` via `stubRequestClient`).
 * `me` defaults to the full-permission operator identity (`auth.mode = "none"`'s
 * shape); a spec that cares about auth overrides one or both. */
function stubAuth(me: MeResponse | null = OPERATOR_ME_RESPONSE, providers: readonly ProviderSummary[] = []): RequestClientStub {
  return stubRequestClient(hubClient, (method, path) => {
    if (path === '/api/me') return me === null ? stubError(401, { detail: 'not authenticated' }) : me;
    if (path === '/api/auth/providers') return providers;
    // The board/rail/graphs reads unwrap their response as a raw array
    // (`data ?? []`) — `{}` would resolve truthy-but-not-iterable and crash the
    // header/board/explorer's own `for…of`, so these need their real empty shape.
    if (path === '/api/chunks') return { chunks: [], next_cursor: null };
      if (path === '/api/questions' || path === '/api/graphs') return [];
    // `BoardHeader` renders its spend cell whenever the read resolves truthy at all
    // (`{}` included) and calls `.toFixed` on `cost_usd` unconditionally.
    if (path === '/api/spend') return { cost_usd: 0, cost_partial: false };
    if (path === '/api/projects') return { projects: LENS_PROJECTS };
    return {};
  });
}

const LENS_PROJECTS = ['zeta', 'blizzard'].map((slug) => ({
  project_id: `proj_${slug}`,
  slug,
  name: slug[0].toUpperCase() + slug.slice(1),
  description: '',
  revision: 1,
  created_at: '2026-10-01T00:00:00Z',
}));

/** Same shape as {@link stubAuth}, but also captures each `/api/spend` request's
 * full URL (query string included) — unlike `stubRequestClient`'s `CapturedRequest`,
 * which drops it, this spec needs the raw `since`/`until` params to prove the
 * yesterday window reaches the request. Conforms to
 * {@link RequestClientStub} (`requests`/`forRoute` unused, kept trivial) so it can
 * stand in for `authStub` in this file's shared `afterEach`. */
function stubAuthCapturingSpendUrls(): RequestClientStub & { spendUrls: string[] } {
  const spendUrls: string[] = [];
  const previousFetch = globalThis.fetch;
  const fakeFetch = async (input: Request): Promise<Response> => {
    const url = new URL(input.url);
    let body: unknown = {};
    if (url.pathname === '/api/me') body = OPERATOR_ME_RESPONSE;
    else if (url.pathname === '/api/chunks') body = { chunks: [], next_cursor: null };
    else if (url.pathname === '/api/questions' || url.pathname === '/api/graphs') body = [];
    else if (url.pathname === '/api/spend') {
      spendUrls.push(input.url);
      body = { cost_usd: 0, cost_partial: false };
    }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  hubClient.setConfig({ baseUrl: 'http://localhost', fetch: fakeFetch as typeof fetch });
  return {
    spendUrls,
    requests: [],
    forRoute: () => [],
    restore: () => hubClient.setConfig({ baseUrl: '', fetch: previousFetch }),
  };
}

const OIDC: readonly ProviderSummary[] = [{ name: 'oidc-co', display_name: 'Stub SSO', type: 'oidc' }];

/** A signed-in person holding no membership; specs add theirs through `tenants`. */
const MEMBERLESS: MeResponse = {
  user_id: 'usr_1',
  username: 'newcomer',
  display_name: 'Newcomer',
  role: null,
  permissions: [],
  tenants: [],
  hub_admin: false,
};
const ACME_ADMIN: MeTenant = {
  tenant_id: 'ten_a',
  name: 'Acme',
  role: 'admin',
  permissions: ['fleet:view', 'user:manage'],
};
const BETA_GUEST: MeTenant = { tenant_id: 'ten_b', name: 'Beta', role: 'guest', permissions: ['fleet:view'] };

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
        provideTenantInterceptor(),
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

    const header = el.querySelector('[data-testid="board-header"]');
    const nav = el.querySelector('[data-testid="app-nav"]');
    expect(header).toBeTruthy();
    expect(nav).toBeTruthy();
    // Header precedes nav.
    expect(Boolean(header!.compareDocumentPosition(nav!) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);

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

  describe('the yesterday spend window (issue #183)', () => {
    it('opens spendYesterday as [yesterday-midnight, today-midnight), both from the local-day boundary helper', async () => {
      authStub.restore();
      const capture = stubAuthCapturingSpendUrls();
      authStub = capture;

      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/');
      await settle(fixture);

      const todayMidnight = startOfLocalDayIso(Date.now());
      const yesterdayMidnight = startOfPreviousLocalDayIso(Date.now());
      const withUntil = capture.spendUrls.filter((u) => u.includes('until='));
      const withoutUntil = capture.spendUrls.filter((u) => !u.includes('until='));

      // spendToday: since alone, the original open-ended tail.
      expect(withoutUntil.some((u) => u.includes(`since=${encodeURIComponent(todayMidnight)}`))).toBe(true);
      // spendYesterday: both bounds, excluding today's own spend by construction.
      expect(
        withUntil.some(
          (u) =>
            u.includes(`since=${encodeURIComponent(yesterdayMidnight)}`) &&
            u.includes(`until=${encodeURIComponent(todayMidnight)}`),
        ),
      ).toBe(true);
    });
  });

  describe('the spend windows across local midnight', () => {
    afterEach(() => vi.useRealTimers());

    it('re-opens spendToday and spendYesterday on the next day once the minute tick crosses midnight', async () => {
      let nowMs = new Date(2026, 6, 16, 23, 59, 30).getTime();
      TestBed.overrideProvider(FLEET_CLOCK, { useValue: () => nowMs });
      vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
      authStub.restore();
      const capture = stubAuthCapturingSpendUrls();
      authStub = capture;

      const fixture = TestBed.createComponent(App);
      await TestBed.inject(Router).navigateByUrl('/');
      await settle(fixture);

      const sinceOf = (day: Date): string => `since=${encodeURIComponent(day.toISOString())}`;
      expect(capture.spendUrls.some((u) => u.includes(sinceOf(new Date(2026, 6, 16))))).toBe(true);

      nowMs = new Date(2026, 6, 17, 0, 0, 30).getTime();
      vi.advanceTimersByTime(60_000);
      await settle(fixture);

      const withUntil = capture.spendUrls.filter((u) => u.includes('until='));
      const withoutUntil = capture.spendUrls.filter((u) => !u.includes('until='));
      expect(withoutUntil.some((u) => u.includes(sinceOf(new Date(2026, 6, 17))))).toBe(true);
      expect(
        withUntil.some(
          (u) =>
            u.includes(sinceOf(new Date(2026, 6, 16))) &&
            u.includes(`until=${encodeURIComponent(new Date(2026, 6, 17).toISOString())}`),
        ),
      ).toBe(true);
    });

    it('does not re-request the spend windows when the one-second tick stays within the day', async () => {
      const nowMs = new Date(2026, 6, 16, 12, 0, 0).getTime();
      let elapsedMs = 0;
      TestBed.overrideProvider(FLEET_CLOCK, { useValue: () => nowMs + elapsedMs });
      vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
      authStub.restore();
      const capture = stubAuthCapturingSpendUrls();
      authStub = capture;

      const fixture = TestBed.createComponent(App);
      await TestBed.inject(Router).navigateByUrl('/');
      await settle(fixture);
      const before = capture.spendUrls.length;

      elapsedMs = 30_000;
      vi.advanceTimersByTime(30_000);
      await settle(fixture);

      expect(capture.spendUrls.length).toBe(before);
    });
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

      // The titlebar is @defer-loaded, and a @defer block does not carry its root's
      // slot attribute through projection — it must still land in the shell's header
      // slot (first in the column), not fall through to the default content slot.
      const shell = el.querySelector('fleet-app-shell .shell');
      expect(shell?.firstElementChild?.tagName.toLowerCase()).toBe('app-mobile-titlebar');
    });
  });

  describe('the project lens selectors', () => {
    afterEach(() => localStorage.removeItem('blizzard.viewport.override'));

    it('feeds the desktop chips from one projects read and switches the lens on a choice', async () => {
      TestBed.inject(ViewportService).setOverride('desktop');
      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/p/blizzard/board');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;
      const chip = (slug: string) => el.querySelector<HTMLElement>(`[data-testid="project-lens-${slug}"]`);

      expect(chip('all')?.getAttribute('aria-pressed')).toBe('false');
      expect(chip('blizzard')?.getAttribute('aria-pressed')).toBe('true');
      expect(authStub.forRoute('/api/projects', 'GET')).toHaveLength(1);

      chip('zeta')!.click();
      await settle(fixture);
      expect(router.url).toBe('/p/zeta/board');
    });

    it('feeds the mobile submenu from the same read', async () => {
      TestBed.inject(ViewportService).setOverride('mobile');
      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/p/blizzard/board');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="mobile-titlebar-menu"]')?.click();
      await settle(fixture);
      document.body.querySelector<HTMLElement>('[data-testid="mobile-titlebar-project"]')?.click();
      await settle(fixture);
      const item = (slug: string) => document.body.querySelector<HTMLElement>(`[data-testid="mobile-titlebar-project-${slug}"]`);

      expect(item('blizzard')?.getAttribute('aria-checked')).toBe('true');
      item('all')!.click();
      await settle(fixture);
      expect(router.url).toBe('/board');
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

    it('renders only the no-access screen for an identity holding no membership', async () => {
      authStub.restore();
      authStub = stubAuth(MEMBERLESS, OIDC);

      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="no-access"]')?.textContent).toContain(
        'Reach out to your administrator for access.',
      );
      expect(el.querySelector('[data-testid="no-access-username"]')?.textContent).toContain('newcomer');
      expect(el.querySelector('[data-testid="board-header"]')).toBeNull();
      expect(el.querySelector('[data-testid="app-nav"]')).toBeNull();
      expect(el.querySelector('[data-testid="tenant-picker"]')).toBeNull();
    });

    it('renders the no-access screen for a hub administrator holding no membership', async () => {
      authStub.restore();
      authStub = stubAuth({ ...MEMBERLESS, hub_admin: true, permissions: ['tenant:admin'] }, OIDC);

      const fixture = TestBed.createComponent(App);
      await TestBed.inject(Router).navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="no-access"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="board-header"]')).toBeNull();
    });

    it('logs out from the no-access screen and lands back on /login', async () => {
      authStub.restore();
      let loggedOut = false;
      authStub = stubRequestClient(hubClient, (method, path) => {
        if (path === '/api/me') return loggedOut ? stubError(401, { detail: 'not authenticated' }) : MEMBERLESS;
        if (path === '/api/auth/providers') return [];
        if (path === '/api/auth/logout' && method === 'POST') {
          loggedOut = true;
          return {};
        }
        // Once logged out, `authState` briefly still shows whatever route was last
        // active while the redirect to /login lands — same empty-shape need as
        // `stubAuth` above, so that transient render does not crash.
        if (path === '/api/chunks') return { chunks: [], next_cursor: null };
        if (path === '/api/questions' || path === '/api/graphs') return [];
        return {};
      });

      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="no-access-logout"]')?.click();
      await settle(fixture);

      expect(router.url).toBe('/login');
    });
  });

  describe('the tenant, settled above the shell', () => {
    afterEach(() => {
      sessionStorage.clear();
      localStorage.clear();
    });

    it('enters the only membership with no choice, names it on every call and in the header, never in a URL', async () => {
      authStub.restore();
      authStub = stubAuth({ ...MEMBERLESS, tenants: [ACME_ADMIN] }, OIDC);

      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/');
      await settle(fixture);
      await router.navigateByUrl('/graphs');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="tenant-picker"]')).toBeNull();
      expect(el.querySelector('[data-testid="board-header"]')?.textContent).toContain('Acme');
      expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBe('ten_a');
      expect(localStorage.getItem(LAST_TENANT_KEY)).toBe('ten_a');
      const afterSettle = authStub.requests.filter((r) => r.path === '/api/graphs');
      expect(afterSettle.length).toBeGreaterThan(0);
      expect(afterSettle.every((r) => r.headers['x-blizzard-tenant'] === 'ten_a')).toBe(true);
      expect(router.url).not.toContain('ten_a');
    });

    it('offers the picker, outside the shell, for several memberships with no remembered choice', async () => {
      authStub.restore();
      authStub = stubAuth({ ...MEMBERLESS, tenants: [ACME_ADMIN, BETA_GUEST] }, OIDC);

      const fixture = TestBed.createComponent(App);
      await TestBed.inject(Router).navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelectorAll('[data-testid="tenant-picker-option"]')).toHaveLength(2);
      expect(el.querySelector('[data-testid="board-header"]')).toBeNull();
      expect(el.querySelector('[data-testid="app-nav"]')).toBeNull();
    });

    it("renders the shell in the chosen tenant, with that membership's permissions, and remembers it", async () => {
      authStub.restore();
      authStub = stubAuth({ ...MEMBERLESS, tenants: [ACME_ADMIN, BETA_GUEST] }, OIDC);

      const fixture = TestBed.createComponent(App);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      el.querySelectorAll<HTMLElement>('[data-testid="tenant-picker-option"]')[1]?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="tenant-picker"]')).toBeNull();
      expect(el.querySelector('[data-testid="board-header"]')?.textContent).toContain('Beta');
      // A guest in Beta, though an admin in Acme: no Admin tab.
      expect(el.querySelector('[data-testid="nav-admin"]')).toBeNull();
      expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBe('ten_b');
      expect(localStorage.getItem(LAST_TENANT_KEY)).toBe('ten_b');
      const counts = authStub.requests.filter((r) => r.path === '/api/chunk-counts');
      expect(counts.at(-1)?.headers['x-blizzard-tenant']).toBe('ten_b');
    });

    it("opens straight into the browser's last choice while it is still a membership", async () => {
      localStorage.setItem(LAST_TENANT_KEY, 'ten_a');
      authStub.restore();
      authStub = stubAuth({ ...MEMBERLESS, tenants: [ACME_ADMIN, BETA_GUEST] }, OIDC);

      const fixture = TestBed.createComponent(App);
      await TestBed.inject(Router).navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="tenant-picker"]')).toBeNull();
      expect(el.querySelector('[data-testid="board-header"]')?.textContent).toContain('Acme');
      expect(el.querySelector('[data-testid="nav-admin"]')).toBeTruthy();
      expect(authStub.requests.every((r) => r.headers['x-blizzard-tenant'] === 'ten_a')).toBe(true);
    });

    /** The hub-level routes — the only ones the board may call before it has entered a tenant. */
    const HUB_LEVEL = new Set(['/api/me', '/api/auth/providers', '/api/health']);
    const tenantScoped = () => authStub.requests.filter((r) => !HUB_LEVEL.has(r.path));

    it('sends nothing tenant-scoped while the picker shows, and forgets a remembered tenant that is gone', async () => {
      sessionStorage.setItem(TAB_TENANT_KEY, 'ten_gone');
      localStorage.setItem(LAST_TENANT_KEY, 'ten_gone');
      authStub.restore();
      authStub = stubAuth({ ...MEMBERLESS, tenants: [ACME_ADMIN, BETA_GUEST] }, OIDC);

      const fixture = TestBed.createComponent(App);
      await TestBed.inject(Router).navigateByUrl('/');
      await settle(fixture);

      expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="tenant-picker"]')).toBeTruthy();
      expect(tenantScoped()).toEqual([]);
      expect(TestBed.inject(TenantContext).current()).toBeNull();
      expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBeNull();
      expect(localStorage.getItem(LAST_TENANT_KEY)).toBeNull();
    });

    it('sends nothing tenant-scoped while the no-access screen shows', async () => {
      localStorage.setItem(LAST_TENANT_KEY, 'ten_gone');
      authStub.restore();
      authStub = stubAuth(MEMBERLESS, OIDC);

      const fixture = TestBed.createComponent(App);
      await TestBed.inject(Router).navigateByUrl('/events');
      await settle(fixture);

      expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="no-access"]')).toBeTruthy();
      expect(tenantScoped()).toEqual([]);
      expect(localStorage.getItem(LAST_TENANT_KEY)).toBeNull();
    });

    it('sends nothing tenant-scoped on the way to sign-in', async () => {
      authStub.restore();
      authStub = stubAuth(null, OIDC);

      const fixture = TestBed.createComponent(App);
      await TestBed.inject(Router).navigateByUrl('/');
      await settle(fixture);

      expect(TestBed.inject(Router).url).toBe('/login');
      expect(tenantScoped()).toEqual([]);
    });

    it("names the entered tenant on the header's own reads from the very first", async () => {
      authStub.restore();
      authStub = stubAuth({ ...MEMBERLESS, tenants: [ACME_ADMIN] }, OIDC);

      const fixture = TestBed.createComponent(App);
      await TestBed.inject(Router).navigateByUrl('/');
      await settle(fixture);

      const headerReads = authStub.requests.filter((r) => r.path === '/api/chunk-counts' || r.path === '/api/spend');
      expect(headerReads.length).toBeGreaterThanOrEqual(3);
      expect(headerReads.every((r) => r.headers['x-blizzard-tenant'] === 'ten_a')).toBe(true);
    });

    it('offers the picker when the last choice is no longer a membership', async () => {
      localStorage.setItem(LAST_TENANT_KEY, 'ten_gone');
      authStub.restore();
      authStub = stubAuth({ ...MEMBERLESS, tenants: [ACME_ADMIN, BETA_GUEST] }, OIDC);

      const fixture = TestBed.createComponent(App);
      await TestBed.inject(Router).navigateByUrl('/');
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="tenant-picker"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="board-header"]')).toBeNull();
    });
  });

  describe('the Admin nav tab', () => {
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

      // Log out lives inside the header's profile menu — closed by
      // default, so it opens the trigger before the entry is reachable. The
      // menu is a CDK overlay, so it renders on `document.body`
      // rather than inside the fixture.
      el.querySelector<HTMLElement>('[data-testid="app-nav-menu"]')?.click();
      await settle(fixture);
      expect(document.body.querySelector('[data-testid="nav-logout"]')).toBeTruthy();
    });
  });
});

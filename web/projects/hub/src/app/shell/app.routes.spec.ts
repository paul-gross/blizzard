import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter, withRouterConfig } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  EVENT_SOURCE_FACTORY,
  ViewportService,
  hubClient,
  provideViewportRenavigation,
  type EventSourceFactory,
  type FleetEventSource,
} from 'fleet';
import { type MeResponse } from 'fleet/shell';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { LAST_TENANT_KEY, TAB_TENANT_KEY } from '../core/auth/tenant-context';
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
  let authStub: RequestClientStub;

  beforeEach(async () => {
    // ViewportService's override persists to localStorage — cleared so a prior
    // test's override never bleeds into the next one.
    localStorage.clear();
    // The app root's session gate needs `/api/me` to resolve before it
    // renders `<router-outlet>` at all — stub the full-permission operator identity
    // (`auth.mode = "none"`'s shape).
    authStub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (path === '/api/auth/providers') return [];
      // See `app.spec.ts`'s `stubAuth` for why these need their real empty shape.
      if (path === '/api/chunks') return { chunks: [], next_cursor: null };
      if (path === '/api/questions' || path === '/api/graphs') return [];
      if (path === '/api/spend') return { cost_usd: 0, cost_partial: false };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes, withRouterConfig({ onSameUrlNavigation: 'reload' })),
        provideViewportRenavigation(),
        { provide: EVENT_SOURCE_FACTORY, useValue: (() => new FakeEventSource() as unknown as FleetEventSource) as EventSourceFactory },
      ],
    }).compileComponents();
  });

  afterEach(() => authStub.restore());

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

/**
 * The route table's `/fleet` entry: mobile-only, with no desktop counterpart —
 * a desktop-width hit redirects to `/board`. Exercised through the real
 * router rather than by rendering `RunnersPage` directly, so a regression that
 * breaks the guard or redirect wiring itself is caught here.
 */
describe('the fleet route (mobile-only, redirects to /board on desktop)', () => {
  let authStub: RequestClientStub;

  beforeEach(async () => {
    localStorage.clear();
    authStub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (path === '/api/auth/providers') return [];
      if (path === '/api/chunks') return { chunks: [], next_cursor: null };
      if (path === '/api/questions' || path === '/api/graphs') return [];
      if (path === '/api/runners') return { runners: [] };
      if (path === '/api/spend') return { cost_usd: 0, cost_partial: false };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes, withRouterConfig({ onSameUrlNavigation: 'reload' })),
        provideViewportRenavigation(),
        { provide: EVENT_SOURCE_FACTORY, useValue: (() => new FakeEventSource() as unknown as FleetEventSource) as EventSourceFactory },
      ],
    }).compileComponents();
  });

  afterEach(() => authStub.restore());

  it('renders the mobile Fleet screen at /fleet when forced to mobile', async () => {
    TestBed.inject(ViewportService).setOverride('mobile');
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/fleet');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-fleet-panel"]')).toBeTruthy();
    expect(router.url).toBe('/fleet');
  });

  it('redirects to /board when /fleet is hit at desktop width', async () => {
    TestBed.inject(ViewportService).setOverride('desktop');
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/fleet');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(router.url).toBe('/board');
    expect(el.querySelector('[data-testid="board-shell"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="mobile-fleet-panel"]')).toBeNull();
  });
});

/**
 * The public pages — sign-in with its refusals, and an invitation's landing — render outside the shell whatever the
 * session, and an unauthenticated read never redirects away from them. The hub lands an accepted invitation on
 * `/invite/accepted?tenant=…`, which enters the invited tenant.
 */
describe('the public pages (sign-in refusals and invitations)', () => {
  const GITHUB = [{ name: 'github', display_name: 'GitHub', type: 'github' }];
  const TWO_TENANTS: MeResponse = {
    ...OPERATOR_ME_RESPONSE,
    role: null,
    tenants: [
      { tenant_id: 'ten_acme', name: 'Acme', role: 'admin', permissions: ['fleet:view'] },
      { tenant_id: 'ten_globex', name: 'Globex', role: 'guest', permissions: ['fleet:view'] },
    ],
  };
  let authStub: RequestClientStub;
  let me: MeResponse | null;

  beforeEach(async () => {
    localStorage.clear();
    sessionStorage.clear();
    me = null;
    authStub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/me') return me ?? stubError(401, { detail: 'authentication required' });
      if (path === '/api/auth/providers') return GITHUB;
      if (path === '/api/chunks') return { chunks: [], next_cursor: null };
      if (path === '/api/questions' || path === '/api/graphs') return [];
      if (path === '/api/spend') return { cost_usd: 0, cost_partial: false };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes, withRouterConfig({ onSameUrlNavigation: 'reload' })),
        provideViewportRenavigation(),
        { provide: EVENT_SOURCE_FACTORY, useValue: (() => new FakeEventSource() as unknown as FleetEventSource) as EventSourceFactory },
      ],
    }).compileComponents();
    TestBed.inject(ViewportService).setOverride('desktop');
  });

  afterEach(() => authStub.restore());

  async function open(url: string) {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl(url);
    await settle(fixture);
    return { el: fixture.nativeElement as HTMLElement, router };
  }

  it('lands a signed-out person on an invitation, whose sign-in carries the token', async () => {
    const { el, router } = await open('/invite/tok_1');

    expect(router.url).toBe('/invite/tok_1');
    expect(el.querySelector('[data-testid="invite-page"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="login-provider-github"]')?.getAttribute('href')).toBe(
      '/api/auth/github/authorize?return_to=%2F&invite=tok_1',
    );
  });

  it('shows a signed-in person the invitation outside the shell, so their account goes through the same check', async () => {
    me = TWO_TENANTS;
    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_acme');
    const { el, router } = await open('/invite/tok_1');

    expect(router.url).toBe('/invite/tok_1');
    expect(el.querySelector('[data-testid="invite-page"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="board-header"]')).toBeNull();
  });

  it('shows a refused sign-in its reason and keeps the refusal in the URL', async () => {
    const { el, router } = await open('/login?refusal=invitation_email_mismatch');

    expect(router.url).toBe('/login?refusal=invitation_email_mismatch');
    expect(el.querySelector('[data-testid="login-refusal"]')?.textContent).toContain(
      'This invitation is for a different email address.',
    );
  });

  it('lands an accepted invitation in the invited tenant, among several, and leaves the landing URL', async () => {
    me = TWO_TENANTS;
    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_acme');
    localStorage.setItem(LAST_TENANT_KEY, 'ten_acme');
    const { el, router } = await open('/invite/accepted?tenant=ten_globex');

    expect(router.url.startsWith('/invite')).toBe(false);
    expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBe('ten_globex');
    expect(localStorage.getItem(LAST_TENANT_KEY)).toBe('ten_globex');
    expect(el.querySelector('[data-testid="board-header-tagline"]')?.textContent?.trim()).toBe('Globex');
  });

  it('settles a landing naming a tenant the person is not in like any stale choice', async () => {
    me = TWO_TENANTS;
    const { el, router } = await open('/invite/accepted?tenant=ten_elsewhere');

    expect(router.url.startsWith('/invite')).toBe(false);
    expect(el.querySelector('[data-testid="tenant-picker"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="board-header"]')).toBeNull();
  });
});

/**
 * The admin surface opens on the members of the tenant the board acts in, and the users page's former path lands
 * there too, so older links still work.
 */
describe('the admin members route', () => {
  let authStub: RequestClientStub;

  beforeEach(async () => {
    localStorage.clear();
    authStub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (path === '/api/auth/providers') return [];
      if (path === '/api/members') return [];
      if (path === '/api/chunks') return { chunks: [], next_cursor: null };
      if (path === '/api/questions' || path === '/api/graphs') return [];
      if (path === '/api/runners') return { runners: [] };
      if (path === '/api/spend') return { cost_usd: 0, cost_partial: false };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes, withRouterConfig({ onSameUrlNavigation: 'reload' })),
        provideViewportRenavigation(),
        { provide: EVENT_SOURCE_FACTORY, useValue: (() => new FakeEventSource() as unknown as FleetEventSource) as EventSourceFactory },
      ],
    }).compileComponents();
    TestBed.inject(ViewportService).setOverride('desktop');
  });

  afterEach(() => authStub.restore());

  async function open(url: string) {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl(url);
    await settle(fixture);
    return { router, el: fixture.nativeElement as HTMLElement };
  }

  it('opens /admin on the members page, reading GET /api/members', async () => {
    const { router, el } = await open('/admin');

    expect(router.url).toBe('/admin/members');
    expect(el.querySelector('[data-testid="members-table"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="admin-tab-members"]')?.textContent).toContain('Members');
    expect(authStub.forRoute('/api/members', 'GET').length).toBeGreaterThan(0);
  });

  it('lands the former /admin/users on the members page', async () => {
    const { router, el } = await open('/admin/users');

    expect(router.url).toBe('/admin/members');
    expect(el.querySelector('[data-testid="members-table"]')).toBeTruthy();
  });
});

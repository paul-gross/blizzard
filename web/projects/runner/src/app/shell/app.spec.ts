import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { EVENT_SOURCE_FACTORY, ViewportService, type EventSourceFactory, type FleetEventSource } from 'fleet';
import {
  capacitiesView,
  hostEnvironmentView,
  hostRegistrationView,
  hostRunnerView,
  hubConnectivityView,
  type RequestClientStub,
  runnerDashboardView,
  runnerStatusView,
  servedProjectStatusView,
  settle,
  stubError,
  stubRunnerClient,
} from 'fleet/testing';

import { App } from './app';
import { routes } from './app.routes';
import { provideFlags } from 'fleet';
import { runnerFlagsRead } from '../core/flags.query';
import { PanelFilters } from '../core/panel-filters';

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

/** One serving runner, `r1` (`rn_1`), on the host. */
const RUNNERS = {
  items: [hostRunnerView({ runner_id: 'rn_1', name: 'r1', bindings: [{ project: 'p', workspace_id: 'ws', agents: 4 }], max_agents: 4 })],
};

/** `r1`'s dashboard — reaching its hub, nothing open. */
const DASHBOARD = runnerDashboardView({
  runner: runnerStatusView({
    runner_id: 'rn_1',
    runner_name: 'r1',
    workspace_id: 'ws',
    capacities: capacitiesView({ bindings: [{ project: 'p', project_id: 'proj_p', workspace_id: 'ws', agents: 4, used: 0, free: 4 }] }),
    hub: hubConnectivityView({ endpoint: 'http://127.0.0.1:8421' }),
    served_projects: [servedProjectStatusView({ entry: 'p', project_id: 'proj_p', slug: 'p', name: 'P', color: 'slate' })],
  }),
});

/** The host's own reads, beyond its runners. */
const HOST: Parameters<typeof stubRunnerClient>[0] = {
  'GET /api/host/environments': () => ({ workspaces: [{ workspace_id: 'ws', items: [hostEnvironmentView({ environment_id: 'e1' })] }] }),
  'GET /api/host/workspaces': () => ({ items: [{ workspace_id: 'ws', max_environments: 1 }] }),
  'GET /api/host/subscriptions': () => ({ items: [] }),
  'GET /api/host/registration': () =>
    hostRegistrationView({ host_id: 'hs_1', tenant_id: 'ten_1', host_name: 'xps', held_runner_ids: ['rn_1'] }),
};

describe('runner App', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    // The shell's pages read the host's runners, then fan out per runner — one runner and
    // otherwise empty answers keep this shell-level test independent of each page's own
    // read behavior (covered by the pages' own specs).
    stub = stubRunnerClient({
      ...HOST,
      'GET /api/host/runners': () => RUNNERS,
      'GET /api/auth/session': () => ({ auth_enabled: false, username: null }),
      'GET /api/flags': () => ({ flags: {}, overridden: [] }),
      'GET /runners/{runner_id}/api/dashboard': (_request, { runner_id }) =>
        runner_id === 'rn_1' ? DASHBOARD : stubError(404, { detail: 'unknown runner' }),
      'GET /runners/{runner_id}/api/chunks/{chunk_id}': () => stubError(404, { detail: 'unknown chunk' }),
      'GET /runners/{runner_id}/api/leases': () => ({ items: [] }),
      'GET /runners/{runner_id}/api/facts': () => ({ items: [] }),
      'GET /runners/{runner_id}/api/asks': () => ({ items: [] }),
      'GET /runners/{runner_id}/api/escalations': () => ({ items: [] }),
      'GET /runners/{runner_id}/api/takeovers': () => ({ items: [] }),
    });
    const factory: EventSourceFactory = () => new FakeEventSource() as unknown as FleetEventSource;
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideFlags(runnerFlagsRead),
        provideRouter(routes),
        // `App` starts `RunnerLiveUpdates` unconditionally —
        // a fake transport so the stream opens without ever reaching real `fetch`.
        { provide: EVENT_SOURCE_FACTORY, useValue: factory },
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  async function open(url: string) {
    const router = TestBed.inject(Router);
    const fixture = TestBed.createComponent(App);
    const resolved = await router.navigateByUrl(url);
    await settle(fixture);
    return { router, fixture, resolved, el: fixture.nativeElement as HTMLElement };
  }

  it('lands the root on the board, with the Board tab active and the filters in the tab strip', async () => {
    const { router, el } = await open('/');

    expect(router.url).toBe('/board');
    expect(el.querySelector('app-panel')).toBeTruthy();
    expect(el.querySelector('[data-testid="nav-board"]')?.classList.contains('active')).toBe(true);
    expect(el.querySelector('[data-testid="app-nav"] [data-testid="runner-filter"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="app-nav"] [data-testid="project-filter"]')).toBeTruthy();
  });

  it('lands an unknown path on the board', async () => {
    const { router } = await open('/nowhere');

    expect(router.url).toBe('/board');
  });

  it('mounts the board with its selection kept in the URL', async () => {
    const { router, el, resolved } = await open('/board?chunk=ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');

    expect(resolved).toBe(true);
    expect(router.url).toBe('/board?chunk=ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
    expect(el.querySelector('[data-testid="local-panel"]')).toBeTruthy();
  });

  it('resolves the events page to the full-width fact log, without mounting the board', async () => {
    const { el, resolved } = await open('/events');

    expect(resolved).toBe(true);
    expect(el.querySelector('[data-testid="fact-log"]')).toBeTruthy();
    expect(el.querySelector('app-panel')).toBeNull();
  });

  it('resolves the host page, with the Host tab active', async () => {
    const { el } = await open('/host');

    expect(el.querySelector('[data-testid="host-page"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="nav-host"]')?.classList.contains('active')).toBe(true);
    expect(el.querySelector('[data-testid="nav-board"]')?.classList.contains('active')).toBe(false);
  });

  it('redirects the old machine page to the host page, keeping the filters', async () => {
    const { router, el } = await open('/machine?runner=r1');

    expect(router.url).toBe('/host?runner=r1');
    expect(el.querySelector('[data-testid="host-page"]')).toBeTruthy();
  });

  describe('a per-runner URL', () => {
    it('redirects to its flat page, naming the runner in ?runner= and keeping the query', async () => {
      const { router, el } = await open('/runners/rn_1/board?chunk=ch_1');

      expect(router.url).toBe('/board?chunk=ch_1&runner=r1');
      expect(TestBed.inject(PanelFilters).runner()).toBe('r1');
      expect(el.querySelector('app-panel')).toBeTruthy();
    });

    it('keeps the rest of the path', async () => {
      const { router } = await open('/runners/rn_1/board/chunk/ch_1');

      expect(router.url).toBe('/board/chunk/ch_1?runner=r1');
    });

    it('lands a bare runner on the board', async () => {
      const { router } = await open('/runners/rn_1');

      expect(router.url).toBe('/board?runner=r1');
    });

    it('lands a runner the host no longer lists on All', async () => {
      const { router } = await open('/runners/rn_gone/events');

      expect(router.url).toBe('/events');
      expect(TestBed.inject(PanelFilters).runner()).toBeNull();
    });

    it('lands its machine page on the host page', async () => {
      const { router, el } = await open('/runners/rn_1/machine');

      expect(router.url).toBe('/host?runner=r1');
      expect(el.querySelector('[data-testid="nav-host"]')?.classList.contains('active')).toBe(true);
    });
  });

  describe('the app-root header/nav/content order', () => {
    // Pins the header as app-root chrome via `AppShell`: it precedes the nav
    // strip in DOM order on the board and stays mounted on the events page, matching the
    // hub.
    it('renders the header above the tab strip on the board, not the other way around', async () => {
      const { el } = await open('/board');

      const header = el.querySelector('[data-testid="board-header"]');
      const nav = el.querySelector('[data-testid="app-nav"]');
      expect(header).toBeTruthy();
      expect(nav).toBeTruthy();
      expect(Boolean(header!.compareDocumentPosition(nav!) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
    });

    it('keeps the header mounted on the events page, outside the board route', async () => {
      const { el } = await open('/events');

      expect(el.querySelector('[data-testid="board-header"]')).toBeTruthy();
    });
  });

  it('marks the active route on the nav tabs and carries the filters and selection across a Board → Events → Board round trip', async () => {
    const { router, fixture } = await open('/board?runner=r1&chunk=ch_1');
    let el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="nav-board"]')?.classList.contains('active')).toBe(true);
    expect(el.querySelector('[data-testid="nav-events"]')?.classList.contains('active')).toBe(false);
    expect(el.querySelector('[data-testid="nav-host"]')?.classList.contains('active')).toBe(false);

    el.querySelector<HTMLElement>('[data-testid="nav-events"]')?.click();
    await settle(fixture);
    el = fixture.nativeElement as HTMLElement;

    expect(router.url).toBe('/events?runner=r1&chunk=ch_1');
    expect(el.querySelector('[data-testid="nav-events"]')?.classList.contains('active')).toBe(true);

    el.querySelector<HTMLElement>('[data-testid="nav-board"]')?.click();
    await settle(fixture);

    expect(router.url).toBe('/board?runner=r1&chunk=ch_1');
  });

  describe('mobile mode', () => {
    it('renders the persistent bottom tab bar and the filters rows instead of the top nav', async () => {
      TestBed.inject(ViewportService).setOverride('mobile');
      const { el } = await open('/board');

      expect(el.querySelector('[data-testid="app-nav"]')).toBeNull();
      expect(el.querySelector('[data-testid="local-panel-mobile-tab-bar"]')).toBeTruthy();
      expect(el.querySelector('app-panel-filters.mobile [data-testid="runner-filter"]')).toBeTruthy();
      expect(el.querySelector('app-panel-filters.mobile [data-testid="project-filter"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="tab-board"]')?.classList.contains('on')).toBe(true);
      expect(el.querySelector('[data-testid="tab-host"]')?.classList.contains('on')).toBe(false);
      expect(el.querySelector('[data-testid="tab-events"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="tab-asks-runner"]')?.hasAttribute('disabled')).toBe(true);
    });
  });
});

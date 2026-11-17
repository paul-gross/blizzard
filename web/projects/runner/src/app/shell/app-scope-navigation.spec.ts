import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { EVENT_SOURCE_FACTORY, type EventSourceFactory, type FleetEventSource, runnerClient, type runnerApi, ViewportService } from 'fleet';
import {
  chunkDetail,
  chunkNeighborView,
  chunkNeighborhoodView,
  hostRegistrationView,
  hostRunnerView,
  type RequestClientStub,
  settle,
  stubRunnerClient,
} from 'fleet/testing';

import { appConfig } from './app.config';
import { routes } from './app.routes';

/** A do-nothing EventSource, so the board's live updates open without a real stream. */
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

const CHUNK = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
const NEIGHBOR = 'ch_01KXKVVF1J3D6H6VYZ3XYNR2R2';

function lease(runnerChunk: string, projectId: string, leaseId: string): runnerApi.LeaseView {
  return {
    lease_id: leaseId,
    chunk_id: runnerChunk,
    graph_id: 'gr_1',
    node_id: 'nd_build',
    node_name: 'build',
    epoch: 1,
    session_id: 'sess-1',
    pid: 4821,
    environment_id: 'beta',
    workdir: '/ws/beta',
    created_at: '2026-07-16T11:00:00.000Z',
    last_heartbeat_at: '2026-07-16T11:59:26.000Z',
    state: 'running',
    liveness: 'live',
    display_status: 'running',
    closure_healthy: false,
    closed_at: null,
    closure_reason: null,
    stale_after_seconds: 3600,
    resolved_model: null,
    resolved_effort: null,
    harness_version: null,
    git_commits: [],
    harness_id: null,
    project_id: projectId,
  };
}

/** The host's two runners: `r1` (`rn_1`) leasing {@link CHUNK} in project `p`, `r2` (`rn_2`) leasing its neighbor in `q`. */
const RUNNERS = {
  items: [
    hostRunnerView({ runner_id: 'rn_1', name: 'r1', bindings: [{ project: 'p', workspace_id: 'ws', agents: 4 }], max_agents: 4 }),
    hostRunnerView({ runner_id: 'rn_2', name: 'r2', bindings: [{ project: 'q', workspace_id: 'ws', agents: 4 }], max_agents: 4 }),
  ],
};
const LEASES: Record<string, runnerApi.LeaseView[]> = {
  rn_1: [lease(CHUNK, 'p', 'lease_1')],
  rn_2: [lease(NEIGHBOR, 'q', 'lease_2')],
};

/** {@link CHUNK}'s detail, blocking {@link NEIGHBOR} — the General tab's dependency edge to hop along. */
const DETAIL = chunkDetail({
  chunk_id: CHUNK,
  neighborhood: chunkNeighborhoodView({ dependents: [chunkNeighborView({ chunk_id: NEIGHBOR, status: 'running' })] }),
});

/** The filter sets the walk runs under — each kept, exactly, back on the Board. */
const FILTER_SETS: readonly [string, Record<string, string>][] = [
  ['?runner only', { runner: 'r1' }],
  ['?project only', { project: 'p' }],
  ['both filters', { runner: 'r1', project: 'p' }],
  ['neither filter', {}],
];

/** The query of the router's current URL, as a plain record. */
function currentQuery(): Record<string, string> {
  const router = TestBed.inject(Router);
  return router.parseUrl(router.url).queryParams as Record<string, string>;
}

function currentPath(): string {
  return TestBed.inject(Router).url.split('?')[0];
}

/**
 * The runner app's own routes and providers, walking a chunk from its board row to the dock, its full page, a
 * neighbor and back: the panel's `?runner=` and `?project=` filters are the app's scope, so every hop keeps exactly
 * the ones set and drops the page's own drill-down.
 */
describe('runner app scope across the board and the chunk page', () => {
  let stub: RequestClientStub;
  let harness: RouterTestingHarness;

  beforeEach(async () => {
    stub = stubRunnerClient({
      'GET /api/host/runners': () => RUNNERS,
      'GET /api/host/environments': () => ({ workspaces: [] }),
      'GET /api/host/workspaces': () => ({ items: [] }),
      'GET /api/host/subscriptions': () => ({ items: [] }),
      'GET /api/host/registration': () =>
        hostRegistrationView({ host_id: 'hs_1', tenant_id: 'ten_1', host_name: 'xps', held_runner_ids: ['rn_1', 'rn_2'] }),
      'GET /api/auth/session': () => ({ auth_enabled: false, username: null }),
      'GET /api/flags': () => ({ flags: {}, overridden: [] }),
      'GET /runners/{runner_id}/api/leases': (_request, { runner_id }) => ({ items: LEASES[runner_id] ?? [] }),
      'GET /runners/{runner_id}/api/chunks/{chunk_id}': (_request, { chunk_id }) =>
        chunk_id === CHUNK ? DETAIL : chunkDetail({ chunk_id }),
      'GET /runners/{runner_id}/api/chunks/{chunk_id}/work-items': () => ({ items: [] }),
      'GET /runners/{runner_id}/api/facts': () => ({ items: [] }),
      'GET /runners/{runner_id}/api/asks': () => ({ items: [] }),
      'GET /runners/{runner_id}/api/escalations': () => ({ items: [] }),
      'GET /runners/{runner_id}/api/takeovers': () => ({ items: [] }),
    });
    const factory: EventSourceFactory = () => new FakeEventSource() as unknown as FleetEventSource;
    TestBed.configureTestingModule({
      providers: [
        ...appConfig.providers,
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
        { provide: EVENT_SOURCE_FACTORY, useValue: factory },
      ],
    });
    TestBed.inject(ViewportService).setOverride('desktop');
    harness = await RouterTestingHarness.create();
  });

  // `appConfig.providers` registers a real session-recovery interceptor on the module-level `runnerClient`, which
  // `stub.restore()` leaves in place; specs share one worker's client, so a stale one would classify a later file's
  // 401 first and set the sessionStorage mark under it.
  afterEach(() => {
    stub.restore();
    runnerClient.interceptors.response.clear();
    sessionStorage.clear();
  });

  const el = (): HTMLElement => harness.fixture.nativeElement as HTMLElement;

  async function open(url: string): Promise<void> {
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    await settle(harness.fixture);
  }

  async function click(selector: string): Promise<void> {
    const target = el().querySelector<HTMLElement>(selector);
    expect(target, selector).toBeTruthy();
    target!.click();
    await settle(harness.fixture);
    await settle(harness.fixture);
  }

  describe.each(FILTER_SETS)('under %s', (_label, filters) => {
    it.each([
      ['the default layout', {}],
      ['the list layout', { layout: 'list' }],
    ] as const)('walks row → dock → full page → Board in %s, keeping exactly the filters set', async (_layout, layout) => {
      await open(`/board?${new URLSearchParams({ ...filters, ...layout })}`);

      // A card opens on its own click; a list row through its stretched open control.
      await click(`[data-chunk="${CHUNK}"]${'layout' in layout ? ' [data-testid="list-row-open"]' : ''}`);
      expect(currentQuery()).toEqual({ ...filters, ...layout, chunk: CHUNK });

      await click('[data-testid="detail-chunk-ref"]');
      expect(currentPath()).toBe(`/board/chunk/${CHUNK}`);
      expect(el().querySelector('[data-testid="board-chunk-detail"]')).toBeTruthy();

      await click('[data-testid="mobile-chunk-back"]');
      expect(currentPath()).toBe('/board');
      expect(currentQuery()).toEqual({ ...filters, ...layout, chunk: CHUNK });
    });

    it('hops to a neighbor from a full page with only the filters in its query', async () => {
      const drillDown = { tab: 'general', artifact: 'a.1', step: 's1', segment: 'seg', sidechain: 'side', chunk: CHUNK, from: 'sess-1' };
      await open(`/board/chunk/${CHUNK}?${new URLSearchParams({ ...filters, ...drillDown })}`);

      await click('[data-testid="blocks-neighbor"]');

      expect(currentPath()).toBe(`/board/chunk/${NEIGHBOR}`);
      expect(currentQuery()).toEqual(filters);
    });

    it('returns to the Board from backToBoard keeping the filters', async () => {
      await open(`/board/chunk/${CHUNK}?${new URLSearchParams({ ...filters, chunk: CHUNK })}`);

      // `backToBoard` is the page's after-delete navigation; reached here through the routed instance.
      const page = harness.fixture.debugElement.query((node) => node.name === 'fleet-chunk-page').componentInstance as {
        backToBoard: () => void;
      };
      page.backToBoard();
      await settle(harness.fixture);

      expect(currentPath()).toBe('/board');
      expect(currentQuery()).toEqual(filters);
    });
  });

  it("reads a hopped-to neighbor under its own leased runner, even when ?runner= names another", async () => {
    await open(`/board/chunk/${CHUNK}?runner=r1`);

    await click('[data-testid="blocks-neighbor"]');

    expect(currentPath()).toBe(`/board/chunk/${NEIGHBOR}`);
    expect(stub.forRoute(`/runners/rn_2/api/chunks/${NEIGHBOR}`, 'GET').length).toBeGreaterThan(0);
    expect(stub.forRoute(`/runners/rn_1/api/chunks/${NEIGHBOR}`, 'GET')).toHaveLength(0);
  });
});

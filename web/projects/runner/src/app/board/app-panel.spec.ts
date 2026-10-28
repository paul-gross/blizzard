import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi, ViewportService } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { type MockInstance, vi } from 'vitest';

import { LocalPanel } from './app-panel';

/** Matches `GET /runners/{runner_id}/api/chunks/{chunk_id}/work-items` for any runner and chunk. */
const WORK_ITEMS_ROUTE = /^\/runners\/[^/]+\/api\/chunks\/[^/]+\/work-items$/;

const REF = Date.parse('2026-07-16T12:00:00.000Z');

const CHUNK = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
const OTHER_CHUNK = 'ch_01KXKVVF1J3D6H6VYZ3XYNDDDD';
const R2_CHUNK = 'ch_01KXKVVF1J3D6H6VYZ3XYNR2R2';

const LEASE = (overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView => ({
  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
  chunk_id: CHUNK,
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 2,
  session_id: 'sess-77',
  pid: 4821,
  environment_id: 'beta',
  workdir: '/ws/beta',
  created_at: '2026-07-16T11:00:00.000Z',
  last_heartbeat_at: '2026-07-16T11:59:26.000Z',
  state: 'running',
  display_status: 'running',
  closure_healthy: false,
  closed_at: null,
  closure_reason: null,
  stale_after_seconds: 3600,
  harness_id: null,
  project_id: null,
  ...overrides,
});

/** A serving runner on the host, binding one project in one workspace. */
function hostRunner(runnerId: string, name: string, project: string): runnerApi.HostRunnerView {
  return {
    runner_id: runnerId,
    name,
    state: 'serving',
    served: true,
    halted_reasons: [],
    pause: { local: false, hub: false, effective: false, local_reason: null },
    throttle: null,
    bindings: [{ project, workspace_id: 'ws', agents: 4 }],
    agents_in_use: 0,
    max_agents: 4,
  };
}

/** The host's two runners: `r1` serving project `p`, `r2` serving project `q`. */
const RUNNERS = [hostRunner('rn_1', 'r1', 'p'), hostRunner('rn_2', 'r2', 'q')];

/** A minimal-but-complete `DashboardView` — every rail panel reads it per runner, so a test
 * overrides only the section it cares about. */
function dashboardBody(runnerId: string, overrides: Partial<runnerApi.DashboardView> = {}): runnerApi.DashboardView {
  return {
    runner: {
      runner_id: runnerId,
      workspace_id: 'ws',
      pause: { local: false, hub: false, effective: false, local_reason: null },
      throttle: null,
      capacities: { used: 0, agents: 4, free: 4, bindings: [] },
      hub: { endpoint: 'http://127.0.0.1:8421', reachable: true, last_contact_at: null, buffer_depth: 0, hub_version: null, window: null },
      last_tick_at: null,
      gates: [],
      identity: null,
      routing_conflicts: [],
      runner_name: null,
      served_projects: null,
      tenant_id: null,
      tenant_name: null,
      unmatched_bindings: [],
    },
    asks: { items: [] },
    escalations: { items: [] },
    takeovers: { items: [] },
    facts: { items: [] },
    harness_health: { items: [] },
    fleet_summary: null,
    ...overrides,
  };
}

interface Fixture {
  /** Each runner's leases, by runner id; a runner with no entry holds none. */
  readonly leases?: Record<string, unknown[]>;
  /** Each runner's dashboard sections, by runner id. */
  readonly dashboards?: Record<string, Partial<runnerApi.DashboardView>>;
  /** Answers a path ahead of the defaults; `undefined` falls through. */
  readonly extra?: (method: string, path: string) => unknown;
}

/** The panel's whole read surface: the host's runners, then each runner's leases and dashboard under `/runners/{id}`. */
function routes(fixture: Fixture = {}): (method: string, path: string) => unknown {
  return (method, path) => {
    const special = fixture.extra?.(method, path);
    if (special !== undefined) return special;
    if (method !== 'GET') return {};
    if (path === '/api/host/runners') return { items: RUNNERS };
    if (path === '/api/host/environments') return { workspaces: [] };
    const leases = /^\/runners\/([^/]+)\/api\/leases$/.exec(path);
    if (leases) return { items: fixture.leases?.[leases[1]] ?? [] };
    const dashboard = /^\/runners\/([^/]+)\/api\/dashboard$/.exec(path);
    if (dashboard) return dashboardBody(dashboard[1], fixture.dashboards?.[dashboard[1]]);
    return { items: [] };
  };
}

let navigateSpy: MockInstance<Router['navigate']>;

/**
 * A real router (the dock's chunk name is a `routerLink`, and the filters and selection ride
 * in the URL), seeded with `initialQuery` before the panel mounts so its first render already
 * reflects a deep-linked URL. `LocalPanel` is created directly rather than through an outlet,
 * so only the router's query-param state matters, never the matched path. `navigate` is
 * spied (not replaced) so a write genuinely round-trips through the URL.
 */
async function setUp(initialQuery: Record<string, string> = {}): Promise<void> {
  await TestBed.configureTestingModule({
    imports: [LocalPanel],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      provideRouter([{ path: '**', children: [] }]),
    ],
  }).compileComponents();
  const router = TestBed.inject(Router);
  const query = new URLSearchParams(initialQuery).toString();
  await router.navigateByUrl(query ? `/?${query}` : '/');
  navigateSpy = vi.spyOn(router, 'navigate');
}

async function render(initialQuery: Record<string, string> = {}): Promise<HTMLElement> {
  await setUp(initialQuery);
  const fixture = TestBed.createComponent(LocalPanel);
  await settle(fixture);
  // The filters and selection re-render through the router; one more pass settles them.
  await settle(fixture);
  return fixture.nativeElement as HTMLElement;
}

const chunkIds = (root: ParentNode | null, testid = 'chunk-row'): (string | null)[] =>
  Array.from(root?.querySelectorAll(`[data-testid="${testid}"]`) ?? []).map((row) => row.getAttribute('data-chunk-id'));

const ESCALATION = {
  chunk_id: CHUNK,
  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
  node_id: 'nd_build',
  epoch: 2,
  closed_at: '2026-07-16T11:45:00.000Z',
  resume_command: 'cd /ws/beta && claude --resume sess-77',
  cause: null,
  effort: null,
  harness_id: null,
  harness_version: null,
  model: null,
  project_id: null,
  session_name: null,
  wrapped_takeover_command: null,
  closure: null,
};

describe('LocalPanel', () => {
  let stub: RequestClientStub;
  let fixtureRef: ReturnType<typeof TestBed.createComponent<LocalPanel>> | undefined;

  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(REF);
  });

  afterEach(() => {
    stub.restore();
    fixtureRef = undefined;
    vi.restoreAllMocks();
  });

  /** Mounts the panel and keeps its fixture, so a click can settle the same tree. */
  async function mount(initialQuery: Record<string, string> = {}): Promise<HTMLElement> {
    await setUp(initialQuery);
    fixtureRef = TestBed.createComponent(LocalPanel);
    await settle(fixtureRef);
    await settle(fixtureRef);
    return fixtureRef.nativeElement as HTMLElement;
  }

  async function click(target: HTMLElement | null | undefined): Promise<void> {
    target?.click();
    await settle(fixtureRef!);
    await settle(fixtureRef!);
  }

  describe('reads across the host', () => {
    it("reads the host's runners first, then each serving runner's leases under its own id", async () => {
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE()] } }));
      await render();

      expect(stub.forRoute('/api/host/runners', 'GET').length).toBeGreaterThan(0);
      expect(stub.forRoute('/runners/rn_1/api/leases', 'GET')).toHaveLength(1);
      expect(stub.forRoute('/runners/rn_2/api/leases', 'GET')).toHaveLength(1);
      expect(stub.forRoute('/api/leases', 'GET')).toHaveLength(0);
    });

    it("reads each runner's dashboard exactly once for the whole board, however many panels read it", async () => {
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE()] } }));
      await render();

      expect(stub.forRoute('/runners/rn_1/api/dashboard', 'GET')).toHaveLength(1);
      expect(stub.forRoute('/runners/rn_2/api/dashboard', 'GET')).toHaveLength(1);
    });

    it("merges every runner's chunks into one board, each card naming its runner", async () => {
      stub = stubRequestClient(
        runnerClient,
        routes({ leases: { rn_1: [LEASE()], rn_2: [LEASE({ lease_id: 'lease_r2', chunk_id: R2_CHUNK })] } }),
      );
      const el = await render();

      const progress = el.querySelector('[data-testid="lane-progress"]');
      expect(new Set(chunkIds(progress))).toEqual(new Set([CHUNK, R2_CHUNK]));
      const runnerOf = (chunkId: string) =>
        progress?.querySelector(`[data-chunk-id="${chunkId}"] [data-testid="chunk-row-runner"]`)?.textContent?.trim();
      expect(runnerOf(CHUNK)).toBe('r1');
      expect(runnerOf(R2_CHUNK)).toBe('r2');
    });

    it('keeps the other runners on the board when one runner cannot be read', async () => {
      stub = stubRequestClient(
        runnerClient,
        routes({
          leases: { rn_1: [LEASE()] },
          extra: (method, path) => (method === 'GET' && path === '/runners/rn_2/api/leases' ? stubError(503, { detail: 'down' }) : undefined),
        }),
      );
      const el = await render();

      expect(chunkIds(el)).toEqual([CHUNK]);
      expect(el.textContent).not.toContain('CHUNKS UNAVAILABLE');
    });
  });

  describe('the filters narrow the board', () => {
    const BOTH = {
      leases: { rn_1: [LEASE({ project_id: 'p' })], rn_2: [LEASE({ lease_id: 'lease_r2', chunk_id: R2_CHUNK, project_id: 'q' })] },
    };

    it("reads only the runner the Runner filter names, and shows only its chunks", async () => {
      stub = stubRequestClient(runnerClient, routes(BOTH));
      const el = await render({ runner: 'r2' });

      expect(chunkIds(el)).toEqual([R2_CHUNK]);
      expect(stub.forRoute('/runners/rn_1/api/leases', 'GET')).toHaveLength(0);
    });

    it("shows only the chunks of the project the Project filter names", async () => {
      stub = stubRequestClient(runnerClient, routes(BOTH));
      const el = await render({ project: 'p' });

      expect(chunkIds(el)).toEqual([CHUNK]);
    });
  });

  describe('async states', () => {
    it('shows a loading line before the first read resolves, not the empty state', async () => {
      stub = stubRequestClient(runnerClient, routes());
      await setUp();
      const fixture = TestBed.createComponent(LocalPanel);
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="chunks-pane"]')?.textContent).toContain('LOADING…');
      expect(el.querySelector('[data-testid="chunks-empty"]')).toBeNull();
      await settle(fixture);
    });

    it('shows the empty state only once every read resolves with no chunk', async () => {
      stub = stubRequestClient(runnerClient, routes());
      const el = await render();

      expect(el.querySelector('[data-testid="chunks-empty"]')?.textContent).toContain('NO CHUNKS ON THESE RUNNERS');
      expect(el.querySelector('[data-testid="board"]')).toBeNull();
    });

    it('shows a degraded line once every runner fails — never the empty state', async () => {
      stub = stubRequestClient(
        runnerClient,
        routes({ extra: (method, path) => (method === 'GET' && /\/api\/leases$/.test(path) ? stubError(503, { detail: 'down' }) : undefined) }),
      );
      const el = await render();

      expect(el.querySelector('[data-testid="chunks-pane"]')?.textContent).toContain('CHUNKS UNAVAILABLE');
      expect(el.querySelector('[data-testid="chunks-empty"]')).toBeNull();
    });
  });

  describe('the board folds leases into chunks', () => {
    it('renders one card per distinct chunk, its older attempts folded away', async () => {
      const older = LEASE({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB', epoch: 1, state: 'closed', closure_reason: 'failed', display_status: 'closed' });
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE(), older] } }));
      const el = await render();

      expect(chunkIds(el)).toEqual([CHUNK]);
    });

    it("carries the chunk's live worker on its card — no separate worker rail", async () => {
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE()] } }));
      const el = await render();

      expect(el.querySelector('[data-testid="chunk-row"] [data-testid="chunk-row-worker"] [data-testid="worker-env"]')?.textContent?.trim()).toBe(
        'beta',
      );
      expect(el.querySelector('[data-testid="agent-row"]')).toBeNull();
    });

    it('keeps a chunk whose session ended on the board, in the Done lane', async () => {
      const done = LEASE({
        lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNCCCC',
        chunk_id: OTHER_CHUNK,
        state: 'closed',
        display_status: 'closed',
        closure_reason: 'transitioned',
        closure_healthy: true,
        closed_at: '2026-07-16T11:30:00.000Z',
      });
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE(), done] } }));
      const el = await render();

      expect(chunkIds(el.querySelector('[data-testid="lane-progress"]'))).toEqual([CHUNK]);
      expect(chunkIds(el.querySelector('[data-testid="lane-done"]'))).toEqual([OTHER_CHUNK]);
      expect(el.querySelector('[data-testid="lane-done"] [data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('COMPLETED');
      expect(el.querySelector('[data-testid="chunk-filter-show-all"]')).toBeNull();
    });

    it('puts an escalated chunk in Needs human, outranking its closed lease', async () => {
      stub = stubRequestClient(
        runnerClient,
        routes({
          leases: { rn_1: [LEASE({ state: 'closed', closure_reason: 'escalated', display_status: 'needs-human' })] },
          dashboards: { rn_1: { escalations: { items: [ESCALATION] } } },
        }),
      );
      const el = await render();

      expect(chunkIds(el.querySelector('[data-testid="lane-needs"]'))).toEqual([CHUNK]);
      expect(el.querySelector('[data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('NEEDS HUMAN');
    });
  });

  describe('the Board/List layout rides the URL', () => {
    it('renders the ranked list for ?layout=list', async () => {
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE()] } }));
      const el = await render({ layout: 'list' });

      expect(el.querySelector('[data-testid="board"]')).toBeNull();
      expect(chunkIds(el.querySelector('[data-testid="board-list"]'), 'list-row')).toEqual([CHUNK]);
    });

    it('writes the layout to the URL, keeping every other param, and drops it again for Board', async () => {
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE()] } }));
      const el = await mount({ chunk: CHUNK });

      await click(el.querySelector<HTMLElement>('[data-testid="board-layout-list"]'));
      expect(navigateSpy).toHaveBeenLastCalledWith([], expect.objectContaining({ queryParams: { layout: 'list' }, queryParamsHandling: 'merge' }));
      expect(TestBed.inject(Router).url).toContain(`chunk=${CHUNK}`);
      expect(el.querySelector('[data-testid="board-list"]')).not.toBeNull();

      await click(el.querySelector<HTMLElement>('[data-testid="board-layout-board"]'));
      expect(navigateSpy).toHaveBeenLastCalledWith([], expect.objectContaining({ queryParams: { layout: null } }));
      expect(el.querySelector('[data-testid="board"]')).not.toBeNull();
    });
  });

  describe('selection drives the detail dock', () => {
    it('shows the SELECT A CHUNK placeholder before anything is selected', async () => {
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE()] } }));
      const el = await render();

      expect(el.querySelector('[data-testid="detail-empty"]')?.textContent).toContain('SELECT A CHUNK');
    });

    it('selecting a chunk card writes it to the URL and opens its execution facts', async () => {
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE()] } }));
      const el = await mount();

      await click(el.querySelector<HTMLElement>('[data-testid="chunk-row"]'));

      expect(navigateSpy).toHaveBeenCalledWith([], expect.objectContaining({ queryParams: { chunk: CHUNK }, queryParamsHandling: 'merge' }));
      expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain(CHUNK);
      const facts = el.querySelector('[data-testid="detail-facts"]')?.textContent ?? '';
      expect(facts).toContain('4821');
      expect(facts).toContain('/ws/beta');
      expect(facts).toContain('sess-77');
      expect(el.querySelector('[data-testid="chunk-row"]')?.classList).toContain('selected');
    });

    it('shows facts for the newest attempt of a multi-attempt chunk', async () => {
      const older = LEASE({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB', epoch: 1, state: 'closed', closure_reason: 'failed', session_id: 'sess-old' });
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE(), older] } }));
      const el = await render({ chunk: CHUNK });

      const facts = el.querySelector('[data-testid="detail-facts"]')?.textContent ?? '';
      expect(facts).toContain('sess-77');
      expect(facts).not.toContain('sess-old');
    });

    it("reads the selected chunk's detail off its own runner", async () => {
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_2: [LEASE({ lease_id: 'lease_r2', chunk_id: R2_CHUNK })] } }));
      await render({ chunk: R2_CHUNK });

      expect(stub.forRoute(`/runners/rn_2/api/chunks/${R2_CHUNK}`, 'GET').length).toBeGreaterThan(0);
      expect(stub.forRoute(`/runners/rn_1/api/chunks/${R2_CHUNK}`, 'GET')).toHaveLength(0);
    });

    it("shows the resume command of the escalation its own runner holds", async () => {
      stub = stubRequestClient(
        runnerClient,
        routes({
          leases: { rn_1: [LEASE({ state: 'closed', closure_reason: 'escalated', display_status: 'needs-human' })] },
          dashboards: {
            rn_1: { escalations: { items: [ESCALATION] } },
            rn_2: { escalations: { items: [{ ...ESCALATION, resume_command: 'another runner’s command' }] } },
          },
        }),
      );
      const el = await render({ chunk: CHUNK });

      const resume = el.querySelector('[data-testid="detail-resume"]')?.textContent;
      expect(resume).toContain('cd /ws/beta && claude --resume sess-77');
      expect(resume).not.toContain('another runner’s command');
    });

    it('hydrates the selection from the URL on load without rewriting it', async () => {
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE()] } }));
      const el = await render({ chunk: CHUNK });

      expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain(CHUNK);
      expect(el.querySelector('[data-testid="chunk-row"]')?.classList).toContain('selected');
      expect(navigateSpy).not.toHaveBeenCalled();
    });

    it('touches no param but its own when a different chunk is selected', async () => {
      const other = LEASE({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNCCCC', chunk_id: OTHER_CHUNK });
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE(), other] } }));
      const el = await mount({ chunk: CHUNK, attempt: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB' });

      await click(el.querySelector<HTMLElement>(`[data-testid="chunk-row"][data-chunk-id="${OTHER_CHUNK}"]`));

      expect(navigateSpy).toHaveBeenCalledWith([], expect.objectContaining({ queryParams: { chunk: OTHER_CHUNK } }));
    });

    it('degrades an unknown chunk id to no selection, leaving the URL untouched', async () => {
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE()] } }));
      const el = await render({ chunk: 'ch_GONE00000000000000000000' });

      expect(el.querySelector('[data-testid="detail-empty"]')?.textContent).toContain('SELECT A CHUNK');
      expect(el.querySelector('[data-testid="chunk-row"]')?.classList).not.toContain('selected');
      expect(navigateSpy).not.toHaveBeenCalled();
    });
  });

  describe('the work-item enrichment stays severable', () => {
    it('renders chunk cards on chunk_id alone when every work-items read 502s', async () => {
      stub = stubRequestClient(
        runnerClient,
        routes({
          leases: { rn_1: [LEASE()] },
          extra: (method, path) => (method === 'GET' && WORK_ITEMS_ROUTE.test(path) ? stubError(502, { detail: 'stubbed route error (502)' }) : undefined),
        }),
      );
      const el = await render();

      expect(chunkIds(el)).toEqual([CHUNK]);
      expect(el.querySelector('[data-testid="chunk-row"]')?.textContent).toContain('C-3YJ9');
    });

    it("renders each linked item's label and title on the card", async () => {
      stub = stubRequestClient(
        runnerClient,
        routes({
          leases: { rn_1: [LEASE()] },
          extra: (method, path) =>
            method === 'GET' && WORK_ITEMS_ROUTE.test(path)
              ? {
                  items: [
                    {
                      source: 'blizzard',
                      ref: '61',
                      label: 'blizzard#61',
                      web_url: 'https://github.com/paul-gross/blizzard/issues/61',
                      fetched_at: '2026-07-16T11:00:00.000Z',
                      title: 'runner machine panel',
                    },
                  ],
                }
              : undefined,
        }),
      );
      const el = await render();

      const title = el.querySelector('[data-testid="chunk-row-title"]')?.textContent;
      expect(title).toContain('blizzard#61');
      expect(title).toContain('runner machine panel');
    });

    it('issues one work-items request per distinct chunk, on its own runner, even with several leases for it', async () => {
      const older = LEASE({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB', epoch: 1, state: 'closed' });
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE(), older] } }));
      await render();

      expect(stub.requests.filter((r) => WORK_ITEMS_ROUTE.test(r.path)).map((r) => r.path)).toEqual([
        `/runners/rn_1/api/chunks/${CHUNK}/work-items`,
      ]);
    });
  });

  describe('the right rail', () => {
    it('renders the hub, host, and open-asks panels beside the board', async () => {
      stub = stubRequestClient(
        runnerClient,
        routes({
          dashboards: {
            rn_1: {
              asks: {
                items: [
                  {
                    question_id: 'qn_01KXKVVF1J3D6H6VYZ3XYNQ777',
                    chunk_id: CHUNK,
                    lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
                    question: 'which branch?',
                    options: [],
                    session_id: 'sess-77',
                    asked_at: '2026-07-16T11:40:00.000Z',
                    harness_id: null,
                    project_id: null,
                  },
                ],
              },
            },
          },
        }),
      );
      const el = await render();

      expect(el.querySelector('[data-testid="hub-link"]')?.textContent).toContain('CONNECTED');
      expect(Array.from(el.querySelectorAll('[data-testid="host-panel-runner-name"]')).map((name) => name.textContent?.trim())).toEqual([
        'r1',
        'r2',
      ]);
      expect(el.querySelector('[data-testid="rail-asks-pane"]')?.textContent).toContain('1 open');
      const ask = el.querySelector('[data-testid="ask-row"]');
      expect(ask?.textContent).toContain('C-3YJ9');
      expect(ask?.textContent).toContain('which branch?');
    });
  });

  describe('the shell picker (ViewportService)', () => {
    afterEach(() => localStorage.removeItem('blizzard.viewport.override'));

    it('desktop mode renders the board and rail layout', async () => {
      stub = stubRequestClient(runnerClient, routes());
      await setUp();
      TestBed.inject(ViewportService).setOverride('desktop');
      const fixture = TestBed.createComponent(LocalPanel);
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="local-panel"]')).not.toBeNull();
      expect(el.querySelector('[data-testid="local-panel-mobile"]')).toBeNull();
    });

    it('mobile mode renders the deferred mobile stack instead, off the same reads', async () => {
      stub = stubRequestClient(runnerClient, routes({ leases: { rn_1: [LEASE()] } }));
      await setUp();
      TestBed.inject(ViewportService).setOverride('mobile');
      const fixture = TestBed.createComponent(LocalPanel);
      await settle(fixture);
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="local-panel-mobile"]')).not.toBeNull();
      expect(el.querySelector('[data-testid="local-panel"]')).toBeNull();
      expect(chunkIds(el)).toEqual([CHUNK]);
    });
  });
});

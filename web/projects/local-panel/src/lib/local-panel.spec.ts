import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi, ViewportService } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { type MockInstance, vi } from 'vitest';

import { LocalPanel } from './local-panel';

/** Matches `GET /api/chunks/{chunk_id}/work-items` for any chunk id. */
const WORK_ITEMS_ROUTE = /^\/api\/chunks\/[^/]+\/work-items$/;

const REF = Date.parse('2026-07-16T12:00:00.000Z');

const LEASE = (overrides: Record<string, unknown> = {}) => ({
  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
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
  closed_at: null,
  closure_reason: null,
  ...overrides,
});

/**
 * A minimal-but-complete `DashboardView` (issue #311) — every rail below
 * `LocalPanel` now reads one shared `GET /api/dashboard` poll rather than one
 * endpoint each, so a test overrides just the section(s) it cares about via
 * `overrides` rather than stubbing a path of its own.
 */
function dashboardBody(overrides: Partial<runnerApi.DashboardView> = {}): runnerApi.DashboardView {
  return {
    runner: {
      runner_id: 'runner-local',
      workspace_id: 'workspace-local',
      pause: { local: false, hub: false, effective: false },
      capacities: { max_agents: 4, used: 0, free: 4 },
      hub: { endpoint: 'http://127.0.0.1:8421', reachable: true, last_contact_at: null, buffer_depth: 0 },
      last_tick_at: null,
    },
    environments: { items: [] },
    asks: { items: [] },
    escalations: { items: [] },
    takeovers: { items: [] },
    facts: { items: [] },
    fleet_summary: null,
    ...overrides,
  };
}

/**
 * A route table for the shell's whole read surface. Leases come from `leases`;
 * the panel's other seven-sections-in-one read (`/api/dashboard`) defaults to
 * {@link dashboardBody}'s empty shape, overridable per section via
 * `dashboard`. Override any other path via `extra` (return `undefined` to
 * fall through to the defaults) — still needed for `/api/leases/{id}
 * /transcript` and `/api/chunks/{id}/work-items`, whose own reads are out of
 * this issue's scope.
 */
function routes(
  leases: unknown[],
  dashboard: Partial<runnerApi.DashboardView> = {},
  extra: (method: string, path: string) => unknown = () => undefined,
): (method: string, path: string) => unknown {
  return (method, path) => {
    const special = extra(method, path);
    if (special !== undefined) return special;
    if (method !== 'GET') return {};
    if (path === '/api/leases') return { items: leases };
    if (path === '/api/dashboard') return dashboardBody(dashboard);
    return { items: [] };
  };
}

let navigateSpy: MockInstance<Router['navigate']>;

/**
 * A real router (issue #318 needs one anyway — `MachineDetailHeader`'s chunk
 * name is now a `routerLink`), seeded with `initialQuery` before the panel
 * mounts so its first render already reflects a deep-linked URL. The catch-all
 * route is a no-op destination: `LocalPanel` is created directly rather than
 * through an outlet, so only the router's shared query-param state (what
 * `injectPanelSelection` reads/writes) matters here, never the matched path.
 * `navigate` is spied (not replaced) so a write genuinely round-trips through
 * `ActivatedRoute.queryParamMap` the way it does in the app, and can still be
 * asserted or checked for a no-write.
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

async function render(initialQuery: Record<string, string> = {}) {
  await setUp(initialQuery);
  const fixture = TestBed.createComponent(LocalPanel);
  await settle(fixture);
  return fixture;
}

describe('LocalPanel', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(REF);
  });

  afterEach(() => {
    stub.restore();
    vi.restoreAllMocks();
  });

  it('polls GET /api/dashboard exactly once for the whole shell, and none of the seven folded-in endpoints (issue #311)', async () => {
    // The desktop layout mounts four separate injectors of the dashboard query
    // (LocalPanel itself, plus EnvList/LocalAsks/LocalInfo via LocalPanelLayout —
    // the app root's own AppHeader is a distinct component tree, out of scope
    // here) — proving one request here is what the issue's rate criterion asks
    // for: TanStack's query-key dedupe, not a single shared read threaded down
    // as an input.
    stub = stubRequestClient(runnerClient, routes([LEASE()]));
    await render();

    expect(stub.forRoute('/api/dashboard', 'GET')).toHaveLength(1);
    for (const path of ['/api/runner', '/api/environments', '/api/asks', '/api/escalations', '/api/takeovers', '/api/facts', '/api/fleet-summary']) {
      expect(stub.forRoute(path, 'GET')).toHaveLength(0);
    }
  });

  it('shows a loading line before the first read resolves, not the empty state', async () => {
    stub = stubRequestClient(runnerClient, routes([]));
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    // Right after creation the stubbed fetch's promise hasn't resolved yet — the
    // panel must read as loading, never as "idle".
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="loading-state"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
    await settle(fixture);
  });

  it('renders the genuinely-idle empty state only once the read resolves with zero leases', async () => {
    stub = stubRequestClient(runnerClient, routes([]));
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="empty-state"]')?.textContent).toContain('NO LIVE LEASES');
    expect(el.querySelector('[data-testid="chunks-empty"]')).not.toBeNull();
  });

  it('shows a distinct degraded line on a 503 — the empty state must never appear on a failed read', async () => {
    stub = stubRequestClient(runnerClient, (method, path) => {
      if (method === 'GET' && path === '/api/leases') return stubError(503, { detail: 'stubbed route error (503)' });
      return { items: [] };
    });
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="error-state"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
  });

  it('renders one agent-row per active lease and none for closed ones — the rail is liveness, not history', async () => {
    const closed = LEASE({
      lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNCCCC',
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYNDDDD',
      state: 'closed',
      closed_at: '2026-07-16T11:30:00.000Z',
      closure_reason: 'transitioned',
    });
    stub = stubRequestClient(runnerClient, routes([LEASE(), closed]));
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="agent-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute('data-lease-id')).toBe('lease_01KXKVVF1J3D6H6VYZ3XYNZPRR');
    expect(el.querySelector('[data-testid="lease-count"]')?.textContent).toContain('1 live');
  });

  it('renders one chunk-row per distinct chunk, hiding a chunk whose newest lease is closed by default (issue #134)', async () => {
    const otherChunk = LEASE({
      lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNCCCC',
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYNDDDD',
      state: 'closed',
      closure_reason: 'transitioned',
    });
    stub = stubRequestClient(runnerClient, routes([LEASE(), otherChunk]));
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="chunk-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute('data-chunk-id')).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
    expect(el.querySelector<HTMLInputElement>('[data-testid="chunk-filter-show-all"]')?.checked).toBe(false);
  });

  it('checking "show all" reveals the closed tail too, newest lease first, distinct chunk folded on chunk_id', async () => {
    const older = LEASE({
      lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB',
      epoch: 1,
      state: 'closed',
      closure_reason: 'failed',
    });
    const otherChunk = LEASE({
      lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNCCCC',
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYNDDDD',
      state: 'closed',
      closure_reason: 'transitioned',
    });
    stub = stubRequestClient(runnerClient, routes([LEASE(), otherChunk, older]));
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLInputElement>('[data-testid="chunk-filter-show-all"]')?.click();
    await settle(fixture);

    const rows = el.querySelectorAll('[data-testid="chunk-row"]');
    // Two distinct chunks — the duplicate (older lease of the first chunk) folds away.
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute('data-chunk-id')).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
    expect(rows[0].querySelector('[data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('RUNNING');
    expect(rows[1].querySelector('[data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('TRANSITIONED');
  });

  it('shows a distinct filtered-empty state naming the hidden count when the filter hides every chunk, not a blank pane (issue #134)', async () => {
    stub = stubRequestClient(runnerClient, routes([LEASE({ state: 'closed', closure_reason: 'transitioned' })]));
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="chunk-row"]')).toHaveLength(0);
    const emptyState = el.querySelector('[data-testid="chunks-empty"]');
    expect(emptyState?.textContent).toContain('1 CHUNK HIDDEN BY THE FILTER');
    expect(emptyState?.textContent).not.toContain('NO CHUNKS ON THIS MACHINE');
  });

  it('derives NEEDS HUMAN from an open escalation, outranking the lease state — visible without checking show-all', async () => {
    stub = stubRequestClient(runnerClient,
      routes([LEASE({ state: 'closed', closure_reason: 'escalated' })], {
        escalations: {
          items: [
            {
              chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
              lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
              node_id: 'nd_build',
              epoch: 2,
              closed_at: '2026-07-16T11:45:00.000Z',
              resume_command: 'cd /ws/beta && claude --resume sess-77',
            },
          ],
        },
      }),
    );
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('NEEDS HUMAN');
  });

  describe('selection drives the machine detail dock', () => {
    it('shows the SELECT A CHUNK placeholder before anything is selected', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE()]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="detail-empty"]')?.textContent).toContain('SELECT A CHUNK');
    });

    it('selecting a chunk row renders its execution facts', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE()]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
      const facts = el.querySelector('[data-testid="detail-facts"]')?.textContent ?? '';
      expect(facts).toContain('L-ZPRR');
      expect(facts).toContain('4821');
      expect(facts).toContain('/ws/beta');
      expect(facts).toContain('sess-77');
    });

    it('shows facts for the newest attempt of a multi-attempt chunk once selected', async () => {
      const older = LEASE({
        lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB',
        epoch: 1,
        state: 'closed',
        closure_reason: 'failed',
        session_id: 'sess-old',
      });
      // Server order: newest active first, then the closed attempt.
      stub = stubRequestClient(runnerClient, routes([LEASE(), older]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
      await settle(fixture);

      const facts = el.querySelector('[data-testid="detail-facts"]')?.textContent ?? '';
      expect(facts).toContain('sess-77');
      expect(facts).not.toContain('sess-old');
    });

    it('selecting a lease row selects its chunk — one shared selection across both rails', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE()]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="agent-row"]')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
      expect(el.querySelector('[data-testid="chunk-row"]')?.classList.contains('selected')).toBe(true);
      expect(el.querySelector('[data-testid="agent-row"]')?.classList.contains('selected')).toBe(true);
    });

    it('shows the escalation resume command in the dock for an escalated selected chunk', async () => {
      stub = stubRequestClient(runnerClient,
        routes([LEASE({ state: 'closed', closure_reason: 'escalated' })], {
          escalations: {
            items: [
              {
                chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
                node_id: 'nd_build',
                epoch: 2,
                closed_at: '2026-07-16T11:45:00.000Z',
                resume_command: 'cd /ws/beta && claude --resume sess-77',
              },
            ],
          },
        }),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;
      // The escalated chunk's newest lease is closed, but NEEDS HUMAN outranks
      // it (`deriveMachineChunkStatus`) — it's visible without checking show-all.
      el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="detail-resume"]')?.textContent).toContain(
        'cd /ws/beta && claude --resume sess-77',
      );
    });
  });

  describe('the URL drives selection (issue #99)', () => {
    const CHUNK = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
    const OLDER = () =>
      LEASE({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB', epoch: 1, state: 'closed', closure_reason: 'failed' });

    it('hydrates the selected chunk from the URL on load, no click — a shareable/refresh-safe link', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE()]));
      const fixture = await render({ chunk: CHUNK });
      const el = fixture.nativeElement as HTMLElement;

      // The detail dock is open on the URL's chunk straight away…
      expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
      expect(el.querySelector('[data-testid="chunk-row"]')?.classList.contains('selected')).toBe(true);
      // …and hydration is a pure read — nothing rewrote the URL.
      expect(navigateSpy).not.toHaveBeenCalled();
    });

    it('writes the chunk selection into the URL when a chunk row is clicked — no full reload', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE()]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
      await settle(fixture);

      // A client-side query-param merge writing only the param this helper owns.
      expect(navigateSpy).toHaveBeenCalledWith(
        [],
        expect.objectContaining({ queryParams: { chunk: CHUNK }, queryParamsHandling: 'merge' }),
      );
    });

    it('touches no param but its own when a different chunk is selected', async () => {
      const otherChunk = LEASE({
        lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNCCCC',
        chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYNDDDD',
      });
      stub = stubRequestClient(runnerClient, routes([LEASE(), otherChunk]));
      // Loaded on the first chunk with its older attempt encoded.
      const fixture = await render({ chunk: CHUNK, attempt: OLDER().lease_id });
      const el = fixture.nativeElement as HTMLElement;

      // `?attempt=` belongs to the chunk detail route (issue #318), which this
      // board neither reads nor writes — selection rewrites `chunk` and nothing else.
      const rows = el.querySelectorAll<HTMLElement>('[data-testid="chunk-row"]');
      rows[1].click();
      await settle(fixture);

      expect(navigateSpy).toHaveBeenCalledWith(
        [],
        expect.objectContaining({ queryParams: { chunk: 'ch_01KXKVVF1J3D6H6VYZ3XYNDDDD' } }),
      );
    });

    it('degrades an unknown chunk id to no-selection without error, leaving the URL untouched', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE()]));
      const fixture = await render({ chunk: 'ch_GONE00000000000000000000' });
      const el = fixture.nativeElement as HTMLElement;

      // The chunk names nothing on this machine — the dock reads as no-selection…
      expect(el.querySelector('[data-testid="detail-empty"]')?.textContent).toContain('SELECT A CHUNK');
      expect(el.querySelector('[data-testid="chunk-row"]')?.classList.contains('selected')).toBe(false);
      // …and the panel never rewrote the URL to "correct" it.
      expect(navigateSpy).not.toHaveBeenCalled();
    });

    it('selecting a lease row writes its chunk to the URL — one shared selection across rails', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE()]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="agent-row"]')?.click();
      await settle(fixture);

      expect(navigateSpy).toHaveBeenCalledWith(
        [],
        expect.objectContaining({ queryParams: { chunk: CHUNK } }),
      );
      // And it round-trips: both rails reflect the now-selected chunk.
      expect(el.querySelector('[data-testid="chunk-row"]')?.classList.contains('selected')).toBe(true);
      expect(el.querySelector('[data-testid="agent-row"]')?.classList.contains('selected')).toBe(true);
    });
  });

  describe('the work-item enrichment stays severable (issue #28)', () => {
    it('renders chunk rows on chunk_id alone when every work-items read 502s — the panel must not depend on the hub', async () => {
      stub = stubRequestClient(runnerClient,
        routes([LEASE()], {}, (method, path) => {
          if (method === 'GET' && WORK_ITEMS_ROUTE.test(path)) return stubError(502, { detail: 'stubbed route error (502)' });
          return undefined;
        }),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelectorAll('[data-testid="chunk-row"]')).toHaveLength(1);
      expect(el.querySelector('[data-testid="chunk-row"]')?.textContent).toContain('C-3YJ9');
    });

    it('renders the pointer label as a link to the work item when web_url arrived', async () => {
      stub = stubRequestClient(runnerClient,
        routes([LEASE()], {}, (method, path) =>
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
        ),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      const link = el.querySelector<HTMLAnchorElement>('[data-testid="chunk-row-title"] a');
      expect(link?.textContent).toContain('blizzard#61');
      expect(link?.href).toBe('https://github.com/paul-gross/blizzard/issues/61');
      expect(el.querySelector('[data-testid="chunk-row-title"]')?.textContent).toContain('runner machine panel');
    });

    it('issues one work-items request per distinct chunk even with several leases for it', async () => {
      const older = LEASE({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB', epoch: 1, state: 'closed' });
      stub = stubRequestClient(runnerClient, routes([LEASE(), older]));
      const fixture = await render();
      await settle(fixture);

      const pmRequests = stub.requests.filter((r) => WORK_ITEMS_ROUTE.test(r.path));
      expect(pmRequests).toHaveLength(1);
    });
  });

  describe('the right rail', () => {
    it('renders the hub link panel off GET /api/dashboard, endpoint and board link included', async () => {
      stub = stubRequestClient(runnerClient,
        routes([], {
          runner: {
            runner_id: 'runner-local',
            workspace_id: 'workspace-local',
            pause: { local: false, hub: false, effective: false },
            capacities: { max_agents: 4, used: 1, free: 3 },
            hub: {
              endpoint: 'http://127.0.0.1:8421',
              reachable: true,
              last_contact_at: '2026-07-16T11:59:30.000Z',
              buffer_depth: 2,
            },
            last_tick_at: '2026-07-16T11:59:45.000Z',
          },
        }),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="hub-endpoint"]')?.textContent).toContain('http://127.0.0.1:8421');
      expect(el.querySelector('[data-testid="hub-link"]')?.textContent).toContain('CONNECTED');
      expect(el.querySelector('[data-testid="hub-last-flush"]')?.textContent).toContain('-30s');
      expect(el.querySelector('[data-testid="hub-buffered"]')?.textContent).toContain('2 events');
      expect(el.querySelector<HTMLAnchorElement>('[data-testid="board-link"]')?.href).toBe('http://127.0.0.1:8421/');
    });

    it('renders open asks with their chunk refs and age', async () => {
      stub = stubRequestClient(runnerClient,
        routes([], {
          asks: {
            items: [
              {
                question_id: 'qn_01KXKVVF1J3D6H6VYZ3XYNQ777',
                chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
                question: 'which branch?',
                options: [],
                session_id: 'sess-77',
                asked_at: '2026-07-16T11:40:00.000Z',
              },
            ],
          },
        }),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      const ask = el.querySelector('[data-testid="ask-row"]');
      expect(ask?.textContent).toContain('C-3YJ9');
      expect(ask?.textContent).toContain('which branch?');
      expect(ask?.textContent).toContain('20m');
    });

    it('renders the held environments with chunk ref and held-for age', async () => {
      stub = stubRequestClient(runnerClient,
        routes([], {
          environments: {
            items: [
              {
                environment_id: 'beta',
                chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                held_since: '2026-07-16T11:18:00.000Z',
              },
            ],
          },
        }),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      const row = el.querySelector('[data-testid="env-row"]');
      expect(row?.getAttribute('data-env-id')).toBe('beta');
      expect(row?.textContent).toContain('C-3YJ9');
      expect(row?.querySelector('[data-testid="env-held-for"]')?.textContent).toContain('42m');
    });

    it('renders a mixed pool — a held row throbbing amber beside a static grey unused row', async () => {
      stub = stubRequestClient(runnerClient,
        routes([], {
          environments: {
            items: [
              {
                environment_id: 'beta',
                chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                held_since: '2026-07-16T11:18:00.000Z',
              },
              { environment_id: 'gamma', chunk_id: null, held_since: null },
            ],
          },
        }),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      const rows = el.querySelectorAll('[data-testid="env-row"]');
      expect(rows).toHaveLength(2);

      const heldRow = rows[0];
      expect(heldRow.getAttribute('data-env-id')).toBe('beta');
      expect(heldRow.getAttribute('data-held')).toBe('true');
      expect(
        heldRow.querySelector('[data-testid="env-beacon"] .beacon')?.classList.contains('active'),
      ).toBe(true);
      expect(heldRow.querySelector('[data-testid="env-held-for"]')?.textContent).toContain('42m');

      const unusedRow = rows[1];
      expect(unusedRow.getAttribute('data-env-id')).toBe('gamma');
      expect(unusedRow.getAttribute('data-held')).toBe('false');
      expect(
        unusedRow.querySelector('[data-testid="env-beacon"] .beacon')?.classList.contains('active'),
      ).toBe(false);
      expect(unusedRow.textContent).not.toContain('C-3YJ9');
      expect(unusedRow.querySelector('[data-testid="env-held-for"]')?.textContent).toBe('');
    });

    it('renders the empty state only when the pool itself is empty', async () => {
      stub = stubRequestClient(runnerClient, routes([], { environments: { items: [] } }));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="env-empty"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="env-row"]')).toBeNull();
    });
  });

  describe('the shell picker (ViewportService)', () => {
    afterEach(() => localStorage.removeItem('blizzard.viewport.override'));

    it('desktop mode renders the existing three-column layout, unchanged', async () => {
      stub = stubRequestClient(runnerClient, routes([]));
      await setUp();
      TestBed.inject(ViewportService).setOverride('desktop');
      const fixture = TestBed.createComponent(LocalPanel);
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="local-panel"]')).not.toBeNull();
      expect(el.querySelector('[data-testid="local-panel-mobile"]')).toBeNull();
    });

    it('mobile mode renders the deferred mobile stack instead, off the same reads', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE()]));
      await setUp();
      TestBed.inject(ViewportService).setOverride('mobile');
      const fixture = TestBed.createComponent(LocalPanel);
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="local-panel-mobile"]')).not.toBeNull();
      expect(el.querySelector('[data-testid="local-panel"]')).toBeNull();
      expect(el.querySelectorAll('[data-testid="agent-row"]')).toHaveLength(1);
    });
  });
});

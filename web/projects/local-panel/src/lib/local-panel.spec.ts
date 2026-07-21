import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, type ParamMap, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, ViewportService } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { BehaviorSubject } from 'rxjs';
import { type Mock, vi } from 'vitest';

import { LocalPanel } from './local-panel';

/**
 * A round-tripping stand-in for the router's query-param binding — the URL that
 * drives selection (issue #99). `LocalPanel` reads `ActivatedRoute.queryParamMap`
 * and writes via `Router.navigate([], { queryParams, queryParamsHandling: 'merge' })`;
 * this stub honors that merge (a `null` value clears a param) and pushes the
 * result back through the same `queryParamMap` subject, so a click genuinely
 * flows URL → state the way the real router would. `navigate` is a spy so writes
 * can be asserted and a no-write ("leave the URL untouched") verified. Seed
 * `initial` to model loading a deep-linked URL.
 */
function makeRouterStub(initial: Record<string, string> = {}): {
  activatedRoute: unknown;
  router: unknown;
  navigate: Mock;
} {
  const params: Record<string, string> = { ...initial };
  const queryParamMap$ = new BehaviorSubject<ParamMap>(convertToParamMap({ ...params }));
  const navigate = vi.fn((_commands: unknown[], extras: { queryParams: Record<string, string | null> }) => {
    for (const [key, value] of Object.entries(extras.queryParams)) {
      if (value === null) delete params[key];
      else params[key] = value;
    }
    queryParamMap$.next(convertToParamMap({ ...params }));
    return Promise.resolve(true);
  });
  const activatedRoute = {
    queryParamMap: queryParamMap$,
    snapshot: {
      get queryParamMap(): ParamMap {
        return queryParamMap$.value;
      },
    },
  };
  return { activatedRoute, router: { navigate }, navigate };
}

/** Matches `GET /api/chunks/{chunk_id}/pm-items` for any chunk id. */
const PM_ITEMS_ROUTE = /^\/api\/chunks\/[^/]+\/pm-items$/;

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
 * A route table for the shell's whole read surface. Leases come from `leases`;
 * every other local read defaults to its empty shape so a test that only cares
 * about the lease/chunk panes stays silent about the rails. Override per-path
 * via `extra` (return `undefined` to fall through to the defaults).
 */
function routes(
  leases: unknown[],
  extra: (method: string, path: string) => unknown = () => undefined,
): (method: string, path: string) => unknown {
  return (method, path) => {
    const special = extra(method, path);
    if (special !== undefined) return special;
    if (method !== 'GET') return {};
    if (path === '/api/leases') return { items: leases };
    return { items: [] };
  };
}

let navigateSpy: Mock;

async function setUp(initialQuery: Record<string, string> = {}): Promise<void> {
  const { activatedRoute, router, navigate } = makeRouterStub(initialQuery);
  navigateSpy = navigate;
  await TestBed.configureTestingModule({
    imports: [LocalPanel],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      { provide: ActivatedRoute, useValue: activatedRoute },
      { provide: Router, useValue: router },
    ],
  }).compileComponents();
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

  it('reflects the connection input in the header', async () => {
    stub = stubRequestClient(runnerClient, routes([]));
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    fixture.componentRef.setInput('connection', 'ok');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('ok');
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

  it('renders one chunk-row per distinct chunk, including closed history, newest lease first', async () => {
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

    const rows = el.querySelectorAll('[data-testid="chunk-row"]');
    // Two distinct chunks — the duplicate (older lease of the first chunk) folds away.
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute('data-chunk-id')).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
    expect(rows[0].querySelector('[data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('RUNNING');
    expect(rows[1].querySelector('[data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('TRANSITIONED');
  });

  it('derives NEEDS HUMAN from an open escalation, outranking the lease state', async () => {
    stub = stubRequestClient(runnerClient,
      routes([LEASE({ state: 'closed', closure_reason: 'escalated' })], (method, path) =>
        method === 'GET' && path === '/api/escalations'
          ? {
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
            }
          : undefined,
      ),
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

    it('selecting a chunk row renders its execution facts and fires the transcript read for its lease', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE()]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain('C-3YJ9');
      const facts = el.querySelector('[data-testid="detail-facts"]')?.textContent ?? '';
      expect(facts).toContain('L-ZPRR');
      expect(facts).toContain('4821');
      expect(facts).toContain('/ws/beta');
      expect(facts).toContain('sess-77');
      expect(stub.forRoute('/api/leases/lease_01KXKVVF1J3D6H6VYZ3XYNZPRR/transcript', 'GET').length).toBeGreaterThan(
        0,
      );
    });

    it('renders one attempt tab per lease of the selected multi-attempt chunk, newest selected', async () => {
      const older = LEASE({
        lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB',
        epoch: 1,
        state: 'closed',
        closure_reason: 'failed',
      });
      // Server order: newest active first, then the closed attempt.
      stub = stubRequestClient(runnerClient, routes([LEASE(), older]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
      await settle(fixture);

      const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
      expect(tabs).toHaveLength(2);
      // Oldest attempt first, newest last and selected by default.
      expect(tabs[0].textContent).toContain('a1');
      expect(tabs[1].textContent).toContain('a2');
      expect(tabs[1].getAttribute('aria-pressed')).toBe('true');
      // The newest attempt's transcript is the default read.
      expect(stub.forRoute('/api/leases/lease_01KXKVVF1J3D6H6VYZ3XYNZPRR/transcript', 'GET').length).toBeGreaterThan(
        0,
      );
    });

    it('selecting a lease row selects its chunk — one shared selection across both rails', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE()]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="agent-row"]')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain('C-3YJ9');
      expect(el.querySelector('[data-testid="chunk-row"]')?.classList.contains('selected')).toBe(true);
      expect(el.querySelector('[data-testid="agent-row"]')?.classList.contains('selected')).toBe(true);
    });

    it('shows the escalation resume command in the dock for an escalated selected chunk', async () => {
      stub = stubRequestClient(runnerClient,
        routes([LEASE({ state: 'closed', closure_reason: 'escalated' })], (method, path) =>
          method === 'GET' && path === '/api/escalations'
            ? {
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
              }
            : undefined,
        ),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="detail-resume"]')?.textContent).toContain(
        'cd /ws/beta && claude --resume sess-77',
      );
    });
  });

  describe('the URL drives selection (issue #99)', () => {
    const CHUNK = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
    const NEWEST_LEASE = 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR';
    const OLDER = () =>
      LEASE({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB', epoch: 1, state: 'closed', closure_reason: 'failed' });

    it('hydrates the selected chunk from the URL on load, no click — a shareable/refresh-safe link', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE()]));
      const fixture = await render({ chunk: CHUNK });
      const el = fixture.nativeElement as HTMLElement;

      // The detail dock is open on the URL's chunk straight away…
      expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain('C-3YJ9');
      expect(el.querySelector('[data-testid="chunk-row"]')?.classList.contains('selected')).toBe(true);
      // …and hydration is a pure read — nothing rewrote the URL.
      expect(navigateSpy).not.toHaveBeenCalled();
    });

    it('hydrates the attempt tab from the URL when one is encoded', async () => {
      // Server order: newest active first, then the closed older attempt.
      stub = stubRequestClient(runnerClient, routes([LEASE(), OLDER()]));
      const fixture = await render({ chunk: CHUNK, attempt: OLDER().lease_id });
      const el = fixture.nativeElement as HTMLElement;

      const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
      // The URL's attempt (the older a1), not the newest default, is active.
      expect(tabs[0].getAttribute('aria-pressed')).toBe('true');
      expect(tabs[1].getAttribute('aria-pressed')).toBe('false');
      expect(stub.forRoute(`/api/leases/${OLDER().lease_id}/transcript`, 'GET').length).toBeGreaterThan(0);
    });

    it('writes the chunk selection into the URL when a chunk row is clicked — no full reload', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE()]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
      await settle(fixture);

      // A client-side query-param merge, clearing any stale attempt.
      expect(navigateSpy).toHaveBeenCalledWith(
        [],
        expect.objectContaining({ queryParams: { chunk: CHUNK, attempt: null }, queryParamsHandling: 'merge' }),
      );
    });

    it('writes the attempt pick into the URL when an attempt tab is clicked, keeping the chunk', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE(), OLDER()]));
      const fixture = await render({ chunk: CHUNK });
      const el = fixture.nativeElement as HTMLElement;

      el.querySelectorAll<HTMLElement>('[data-testid="attempt-tab"]')[0].click();
      await settle(fixture);

      expect(navigateSpy).toHaveBeenCalledWith(
        [],
        expect.objectContaining({ queryParams: { chunk: CHUNK, attempt: OLDER().lease_id } }),
      );
    });

    it('clears a stale attempt when a different chunk is selected', async () => {
      const otherChunk = LEASE({
        lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNCCCC',
        chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYNDDDD',
      });
      stub = stubRequestClient(runnerClient, routes([LEASE(), otherChunk]));
      // Loaded on the first chunk with its older attempt encoded.
      const fixture = await render({ chunk: CHUNK, attempt: OLDER().lease_id });
      const el = fixture.nativeElement as HTMLElement;

      // Selecting the *other* chunk drops the attempt (it belonged to the first).
      const rows = el.querySelectorAll<HTMLElement>('[data-testid="chunk-row"]');
      rows[1].click();
      await settle(fixture);

      expect(navigateSpy).toHaveBeenCalledWith(
        [],
        expect.objectContaining({ queryParams: { chunk: 'ch_01KXKVVF1J3D6H6VYZ3XYNDDDD', attempt: null } }),
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

    it('ignores an attempt id that is not one of the selected chunk’s leases, defaulting to newest', async () => {
      stub = stubRequestClient(runnerClient, routes([LEASE(), OLDER()]));
      const fixture = await render({ chunk: CHUNK, attempt: 'lease_STALE0000000000000000' });
      const el = fixture.nativeElement as HTMLElement;

      const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
      // The stale attempt is not among the chunk's leases → newest is active.
      expect(tabs[1].getAttribute('aria-pressed')).toBe('true');
      expect(stub.forRoute(`/api/leases/${NEWEST_LEASE}/transcript`, 'GET').length).toBeGreaterThan(0);
      // No rewrite — an ignored param is left as-is until the next real pick.
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
        expect.objectContaining({ queryParams: { chunk: CHUNK, attempt: null } }),
      );
      // And it round-trips: both rails reflect the now-selected chunk.
      expect(el.querySelector('[data-testid="chunk-row"]')?.classList.contains('selected')).toBe(true);
      expect(el.querySelector('[data-testid="agent-row"]')?.classList.contains('selected')).toBe(true);
    });
  });

  describe('the PM enrichment stays severable (issue #28)', () => {
    it('renders chunk rows on chunk_id alone when every pm-items read 502s — the panel must not depend on the hub', async () => {
      stub = stubRequestClient(runnerClient,
        routes([LEASE()], (method, path) => {
          if (method === 'GET' && PM_ITEMS_ROUTE.test(path)) return stubError(502, { detail: 'stubbed route error (502)' });
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
        routes([LEASE()], (method, path) =>
          method === 'GET' && PM_ITEMS_ROUTE.test(path)
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

    it('issues one pm-items request per distinct chunk even with several leases for it', async () => {
      const older = LEASE({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB', epoch: 1, state: 'closed' });
      stub = stubRequestClient(runnerClient, routes([LEASE(), older]));
      const fixture = await render();
      await settle(fixture);

      const pmRequests = stub.requests.filter((r) => PM_ITEMS_ROUTE.test(r.path));
      expect(pmRequests).toHaveLength(1);
    });
  });

  describe('the right rail', () => {
    it('renders the hub link panel off GET /api/runner, endpoint and board link included', async () => {
      stub = stubRequestClient(runnerClient,
        routes([], (method, path) =>
          method === 'GET' && path === '/api/runner'
            ? {
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
              }
            : undefined,
        ),
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
        routes([], (method, path) =>
          method === 'GET' && path === '/api/asks'
            ? {
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
              }
            : undefined,
        ),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      const ask = el.querySelector('[data-testid="ask-row"]');
      expect(ask?.textContent).toContain('C-3YJ9');
      expect(ask?.textContent).toContain('which branch?');
      expect(ask?.textContent).toContain('20m');
    });

    it('renders the fact log off the outbound ledger with flush markers', async () => {
      stub = stubRequestClient(runnerClient,
        routes([], (method, path) =>
          method === 'GET' && path === '/api/facts'
            ? {
                items: [
                  {
                    seq: 2,
                    kind: 'completion.submitted',
                    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                    lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
                    created_at: '2026-07-16T11:58:00.000Z',
                    acked_at: null,
                  },
                  {
                    seq: 1,
                    kind: 'lease.minted',
                    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                    lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
                    created_at: '2026-07-16T11:50:00.000Z',
                    acked_at: '2026-07-16T11:50:05.000Z',
                  },
                ],
              }
            : undefined,
        ),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      const rows = el.querySelectorAll('[data-testid="fact-row"]');
      expect(rows).toHaveLength(2);
      expect(rows[0].textContent).toContain('completion.submitted');
      expect(rows[0].textContent).toContain('C-3YJ9');
      expect(rows[0].querySelector('.flush')?.classList.contains('acked')).toBe(false);
      expect(rows[1].querySelector('.flush')?.classList.contains('acked')).toBe(true);
    });

    it('renders the held environments with chunk ref and held-for age', async () => {
      stub = stubRequestClient(runnerClient,
        routes([], (method, path) =>
          method === 'GET' && path === '/api/environments'
            ? {
                items: [
                  {
                    environment_id: 'beta',
                    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                    held_since: '2026-07-16T11:18:00.000Z',
                  },
                ],
              }
            : undefined,
        ),
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
        routes([], (method, path) =>
          method === 'GET' && path === '/api/environments'
            ? {
                items: [
                  {
                    environment_id: 'beta',
                    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                    held_since: '2026-07-16T11:18:00.000Z',
                  },
                  { environment_id: 'gamma', chunk_id: null, held_since: null },
                ],
              }
            : undefined,
        ),
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
      stub = stubRequestClient(runnerClient,
        routes([], (method, path) => (method === 'GET' && path === '/api/environments' ? { items: [] } : undefined)),
      );
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

    it('the viewport toggle sits behind a quiet header menu, reachable in both modes', async () => {
      stub = stubRequestClient(runnerClient, routes([]));
      await setUp();
      const viewport = TestBed.inject(ViewportService);
      const fixture = TestBed.createComponent(LocalPanel);

      viewport.setOverride('desktop');
      await settle(fixture);
      let el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('fleet-viewport-toggle')).toBeNull();
      el.querySelector<HTMLElement>('[data-testid="local-panel-menu"]')?.click();
      await settle(fixture);
      el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('[data-testid="local-panel-menu-panel"] fleet-viewport-toggle')).not.toBeNull();

      viewport.setOverride('mobile');
      await settle(fixture);
      el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('fleet-viewport-toggle')).toBeNull();
      el.querySelector<HTMLElement>('[data-testid="local-panel-mobile-titlebar-menu"]')?.click();
      await settle(fixture);
      el = fixture.nativeElement as HTMLElement;
      expect(
        el.querySelector('[data-testid="local-panel-mobile-titlebar-menu-panel"] fleet-viewport-toggle'),
      ).not.toBeNull();
    });

    it('renders the persistent mobile tab bar with Machine active and Asks/Transcripts inert', async () => {
      stub = stubRequestClient(runnerClient, routes([]));
      await setUp();
      TestBed.inject(ViewportService).setOverride('mobile');
      const fixture = TestBed.createComponent(LocalPanel);
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="local-panel-mobile-tab-bar"]')).not.toBeNull();
      const machine = el.querySelector('[data-testid="tab-machine"]');
      const asks = el.querySelector('[data-testid="tab-asks-runner"]');
      const transcripts = el.querySelector('[data-testid="tab-transcripts"]');
      expect(machine?.textContent).toContain('Machine');
      expect(machine?.classList.contains('on')).toBe(true);
      expect(asks?.hasAttribute('disabled')).toBe(true);
      expect(transcripts?.hasAttribute('disabled')).toBe(true);
    });

    it('shows the local asks open count on the mobile tab bar', async () => {
      const ask = {
        question_id: 'qn_1',
        chunk_id: 'ch_1',
        runner_id: 'r1',
        question: 'a?',
        options: [],
      };
      stub = stubRequestClient(
        runnerClient,
        routes([], (method, path) => (method === 'GET' && path === '/api/asks' ? { items: [ask] } : undefined)),
      );
      await setUp();
      TestBed.inject(ViewportService).setOverride('mobile');
      const fixture = TestBed.createComponent(LocalPanel);
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="tab-asks-runner-badge"]')?.textContent).toBe('1');
    });
  });
});

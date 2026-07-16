import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { LocalPanel } from './local-panel';
import { settle } from './testing/settle';
import { RouteError, type RunnerClientStub, stubRunnerClient } from './testing/stub-runner-client';

/** Matches `GET /api/chunks/{chunk_id}/pm-items` for any chunk id. */
const PM_ITEMS_ROUTE = /^\/api\/chunks\/[^/]+\/pm-items$/;

const LEASE = (overrides: Record<string, unknown> = {}) => ({
  lease_id: 'L-903',
  chunk_id: 'C-125',
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 2,
  session_id: 'sess-77',
  pid: 4821,
  environment_id: 'beta',
  workdir: '/ws/beta',
  created_at: '2026-07-16T11:00:00+00:00',
  last_heartbeat_at: '2026-07-16T11:59:26+00:00',
  state: 'running',
  ...overrides,
});

async function setUp(): Promise<void> {
  await TestBed.configureTestingModule({
    imports: [LocalPanel],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
}

describe('LocalPanel', () => {
  let stub: RunnerClientStub;

  afterEach(() => stub.restore());

  it('reflects the connection input in the header', async () => {
    stub = stubRunnerClient((method, path) => (method === 'GET' && path === '/api/leases' ? { items: [] } : {}));
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    fixture.componentRef.setInput('connection', 'ok');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('ok');
  });

  it('shows a loading line before the first read resolves, not the empty state', async () => {
    stub = stubRunnerClient((method, path) => (method === 'GET' && path === '/api/leases' ? { items: [] } : {}));
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    // Right after creation the stubbed fetch's promise hasn't resolved yet — the
    // query is still pending, so the loading line (not IDLE) must be showing.
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="loading-state"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();

    await settle(fixture);
    expect(el.querySelector('[data-testid="empty-state"]')?.textContent).toContain('RUNNER IDLE');
  });

  it('renders the genuinely-idle empty state only once the read resolves with zero leases', async () => {
    stub = stubRunnerClient((method, path) => (method === 'GET' && path === '/api/leases' ? { items: [] } : {}));
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="empty-state"]')?.textContent).toContain('RUNNER IDLE');
    expect(el.querySelector('[data-testid="error-state"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="agent-row"]')).toHaveLength(0);
  });

  it('renders one agent-row per active lease from GET /api/leases', async () => {
    stub = stubRunnerClient((method, path) =>
      method === 'GET' && path === '/api/leases'
        ? { items: [LEASE(), LEASE({ lease_id: 'L-905', chunk_id: 'C-126', state: 'stale' })] }
        : {},
    );
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="agent-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute('data-lease-id')).toBe('L-903');
    expect(rows[1].getAttribute('data-lease-id')).toBe('L-905');
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
  });

  it('shows a distinct degraded line on a 503 — the empty state must never appear on a failed read', async () => {
    stub = stubRunnerClient((method, path) => {
      if (method === 'GET' && path === '/api/leases') throw new RouteError(503, 'lease store not wired');
      return {};
    });
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="error-state"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="agent-row"]')).toHaveLength(0);
  });

  // ★ The degraded-path spec (issue #28 phase 7) — protects "the machine panel
  // must not depend on the hub" (design/runner/web-app.md) from eroding later.
  // GET /api/leases (hub-free, critical path) succeeds with three leases; every
  // GET /api/chunks/*/pm-items (the severable title read) 502s, as it would with
  // the hub down. The panel must render as if titles were never attempted.
  it('renders three agent-rows on their chunk_id alone when every pm-items read 502s — the panel must not depend on the hub', async () => {
    stub = stubRunnerClient((method, path) => {
      if (method === 'GET' && path === '/api/leases') {
        return {
          items: [
            LEASE({ lease_id: 'L-901', chunk_id: 'C-125' }),
            LEASE({ lease_id: 'L-902', chunk_id: 'C-126' }),
            LEASE({ lease_id: 'L-903', chunk_id: 'C-127' }),
          ],
        };
      }
      if (method === 'GET' && PM_ITEMS_ROUTE.test(path)) throw new RouteError(502, 'hub unreachable');
      return {};
    });
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    // (1) three agent-row elements render
    const rows = el.querySelectorAll('[data-testid="agent-row"]');
    expect(rows).toHaveLength(3);
    // (2) each shows its chunk_id
    expect(rows[0].textContent).toContain('C-125');
    expect(rows[1].textContent).toContain('C-126');
    expect(rows[2].textContent).toContain('C-127');
    // (3) error-state is absent
    expect(el.querySelector('[data-testid="error-state"]')).toBeNull();
    // (4) empty-state is absent
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
    // (5) exactly one pm-items request per distinct chunk id — proving retry:
    // false and that the title read is off the 5s poll interval.
    const pmItemRequests = stub.requests.filter((r) => r.method === 'GET' && PM_ITEMS_ROUTE.test(r.path));
    expect(pmItemRequests.map((r) => r.path).sort()).toEqual([
      '/api/chunks/C-125/pm-items',
      '/api/chunks/C-126/pm-items',
      '/api/chunks/C-127/pm-items',
    ]);
    expect(pmItemRequests).toHaveLength(3);
  });

  // Companion case: the anti-retry-storm guarantee under the leases poll itself —
  // without this, "volatile" (refetchInterval: false, retry: false) is a comment
  // on chunk-title.query.ts, not a property the suite enforces.
  it('never re-issues a pm-items request on the leases poll — one 502 per chunk id total, not one per 5s tick', async () => {
    vi.useFakeTimers();
    try {
      stub = stubRunnerClient((method, path) => {
        if (method === 'GET' && path === '/api/leases') {
          return {
            items: [
              LEASE({ lease_id: 'L-901', chunk_id: 'C-125' }),
              LEASE({ lease_id: 'L-902', chunk_id: 'C-126' }),
              LEASE({ lease_id: 'L-903', chunk_id: 'C-127' }),
            ],
          };
        }
        if (method === 'GET' && PM_ITEMS_ROUTE.test(path)) throw new RouteError(502, 'hub unreachable');
        return {};
      });
      await setUp();
      const fixture = TestBed.createComponent(LocalPanel);
      fixture.detectChanges();
      // Flush the initial reads under fake timers (mirrors leases.query.spec.ts).
      for (let i = 0; i < 8; i += 1) {
        await vi.advanceTimersByTimeAsync(0);
        fixture.detectChanges();
      }

      const pmItemCount = () => stub!.requests.filter((r) => r.method === 'GET' && PM_ITEMS_ROUTE.test(r.path)).length;
      const leaseCount = () => stub!.forRoute('/api/leases', 'GET').length;

      expect(pmItemCount()).toBe(3);
      const initialLeaseCount = leaseCount();

      // Advance past several 5s leases-poll intervals.
      await vi.advanceTimersByTimeAsync(20_000);

      expect(leaseCount()).toBeGreaterThan(initialLeaseCount);
      expect(pmItemCount()).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });

  // Two agents on one chunk is a real shape (a chunk's build and review leases can
  // both be active), and it is the case that proves the "one query per *distinct*
  // chunk id" claim is TanStack cache-key dedup rather than an artifact of every
  // fixture happening to use distinct ids.
  it('issues one pm-items request for two rows sharing a chunk_id — deduped by cache key, not one per row', async () => {
    stub = stubRunnerClient((method, path) => {
      if (method === 'GET' && path === '/api/leases') {
        return {
          items: [
            LEASE({ lease_id: 'L-901', chunk_id: 'C-125', node_name: 'build' }),
            LEASE({ lease_id: 'L-902', chunk_id: 'C-125', node_name: 'review' }),
          ],
        };
      }
      if (method === 'GET' && PM_ITEMS_ROUTE.test(path)) {
        return {
          items: [
            {
              provider: 'github',
              url: 'https://github.com/acme/widget/issues/8',
              label: 'gh:widget#8',
              title: 'Fix the flaky retry',
              fetched_at: 't',
              body: 'x',
              comments: [],
            },
          ],
        };
      }
      return {};
    });
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="agent-row"]')).toHaveLength(2);
    // One request — but both rows still render the title off the one shared cache entry.
    expect(stub.requests.filter((r) => r.method === 'GET' && PM_ITEMS_ROUTE.test(r.path))).toHaveLength(1);
    const titles = el.querySelectorAll('[data-testid="agent-title"]');
    expect(titles).toHaveLength(2);
    expect(titles[0].textContent).toContain('Fix the flaky retry');
    expect(titles[1].textContent).toContain('Fix the flaky retry');
  });

  // Pins `staleTime` + `refetchOnMount: false` *as a pair*. Each masks the other
  // individually (a 0 staleTime refetches nothing while refetchOnMount is false;
  // refetchOnMount: true refetches nothing while the data is fresh), so only a
  // remount against a warm cache can hold the pair honest. This is the plan's cost
  // argument — the title read must not re-ask the hub (2 forge calls per pointer)
  // every time a row is torn down and rebuilt.
  it('does not re-request pm-items when a row remounts against a warm cache — the title is fetched once, not per mount', async () => {
    stub = stubRunnerClient((method, path) => {
      if (method === 'GET' && path === '/api/leases') return { items: [LEASE({ chunk_id: 'C-125' })] };
      if (method === 'GET' && PM_ITEMS_ROUTE.test(path)) {
        return {
          items: [
            {
              provider: 'github',
              url: 'https://github.com/acme/widget/issues/8',
              label: 'gh:widget#8',
              title: 'Fix the flaky retry',
              fetched_at: 't',
              body: 'x',
              comments: [],
            },
          ],
        };
      }
      return {};
    });
    await setUp();
    const pmItemCount = () => stub.requests.filter((r) => r.method === 'GET' && PM_ITEMS_ROUTE.test(r.path)).length;

    const first = TestBed.createComponent(LocalPanel);
    await settle(first);
    expect(pmItemCount()).toBe(1);

    // Tear the panel (and its rows) down and rebuild them against the same, still-warm
    // QueryClient cache — the `@for` track churn / navigate-away-and-back shape.
    first.destroy();
    const second = TestBed.createComponent(LocalPanel);
    await settle(second);

    expect((second.nativeElement as HTMLElement).querySelector('[data-testid="agent-title"]')?.textContent).toContain(
      'Fix the flaky retry',
    );
    expect(pmItemCount()).toBe(1);
  });
});

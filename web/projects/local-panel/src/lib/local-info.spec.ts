import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { LocalInfo } from './local-info';

/** The runner's own hub-link facts off `GET /api/dashboard`'s `runner` section —
 * hub-free, so it resolves even when the fleet-summary forward fails (`fleet_summary:
 * null`, issue #311), letting the strip render its degraded state while the rest of
 * the panel stays lit. */
const RUNNER_STATUS = {
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
};

const COUNTS = { ready: 4, running: 3, waiting: 2, needs: 1 };

/** A full `DashboardView` body — the runner section fixed to {@link RUNNER_STATUS},
 * every other section its empty default, `fleet_summary` set to `fleetSummary`. */
function dashboardBody(fleetSummary: runnerApi.FleetSummaryView | null): runnerApi.DashboardView {
  return {
    runner: RUNNER_STATUS,
    environments: { items: [] },
    asks: { items: [] },
    escalations: { items: [] },
    takeovers: { items: [] },
    facts: { items: [] },
    fleet_summary: fleetSummary,
  };
}

/** Render `LocalInfo` with `/api/dashboard` answered by `fleetSummary` — the canned
 * counts, or `null` for the degraded path (a hub outage is a 200 with a null slot
 * under the aggregate, never a failed request). */
async function render(fleetSummary: runnerApi.FleetSummaryView | null) {
  const stub = stubRequestClient(runnerClient, (method, path) => {
    if (method !== 'GET') return {};
    if (path === '/api/dashboard') return dashboardBody(fleetSummary);
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [LocalInfo],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalInfo);
  await settle(fixture);
  return { fixture, stub };
}

describe('LocalInfo fleet-summary strip', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  it('reads the counts off GET /api/dashboard and renders the four buckets', async () => {
    const { fixture, stub: s } = await render(COUNTS);
    stub = s;
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="fleet-ready"]')?.textContent).toContain('4');
    expect(el.querySelector('[data-testid="fleet-running"]')?.textContent).toContain('3');
    expect(el.querySelector('[data-testid="fleet-waiting"]')?.textContent).toContain('2');
    expect(el.querySelector('[data-testid="fleet-needs"]')?.textContent).toContain('1');
    // Live, not degraded.
    expect(el.querySelector('[data-testid="fleet-strip"]')?.classList.contains('stale')).toBe(false);
    expect(el.querySelector('[data-testid="fleet-age"]')?.textContent).toContain('live');
    // It read through the composed dashboard read, not the hub directly.
    expect(stub.forRoute('/api/dashboard', 'GET').length).toBeGreaterThan(0);
  });

  it('renders a zero count, not a dash, for an empty bucket', async () => {
    const { fixture, stub: s } = await render({ ready: 0, running: 0, waiting: 0, needs: 0 });
    stub = s;
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="fleet-ready"]')?.textContent).toContain('0');
    expect(el.querySelector('[data-testid="fleet-ready"]')?.textContent).not.toContain('—');
  });

  it('degrades to the last-known/dimmed state when the hub forward fails', async () => {
    // A hub outage is a 200 `/api/dashboard` read carrying `fleet_summary: null`
    // (issue #311) — the strip dims and banners "last known", and the rest of the
    // panel (hub-free) is unaffected.
    const { fixture, stub: s } = await render(null);
    stub = s;
    const el = fixture.nativeElement as HTMLElement;

    const strip = el.querySelector('[data-testid="fleet-strip"]');
    expect(strip?.classList.contains('stale')).toBe(true);
    expect(el.querySelector('[data-testid="fleet-age"]')?.textContent).toContain('last known');
    // Never-loaded counts read as a dash, not a fabricated zero.
    expect(el.querySelector('[data-testid="fleet-ready"]')?.textContent).toContain('—');
    // The rest of the panel stays lit — the hub-link facts still render.
    expect(el.querySelector('[data-testid="hub-endpoint"]')?.textContent).toContain('http://127.0.0.1:8421');
  });
});

describe('LocalInfo last-flush/tick ticking (issue #178)', () => {
  let stub: RequestClientStub;

  afterEach(() => {
    stub.restore();
    vi.restoreAllMocks();
  });

  // `shouldAdvanceTime` keeps settle()'s macrotask resolvable while the ticking
  // interval is driven by advanceTimersByTimeAsync — the wait is a jump, not a
  // real second spent in the gating job.
  it('re-renders last flush and tick at least once a second with no new data', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const dateNow = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-07-16T12:00:00.000Z'));
      const { fixture, stub: s } = await render(COUNTS);
      stub = s;
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('[data-testid="hub-last-flush"]')?.textContent).toContain('-30s');
      expect(el.querySelector('.tick')?.textContent).toContain('-15s');

      dateNow.mockReturnValue(Date.parse('2026-07-16T12:01:00.000Z'));
      await vi.advanceTimersByTimeAsync(1_100);
      fixture.detectChanges();

      expect(el.querySelector('[data-testid="hub-last-flush"]')?.textContent).toContain('-1m');
      expect(el.querySelector('.tick')?.textContent).toContain('-1m');
    } finally {
      vi.useRealTimers();
    }
  });
});

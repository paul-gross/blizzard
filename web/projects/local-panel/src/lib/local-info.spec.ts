import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { LocalInfo } from './local-info';

/** The runner's own hub-link facts off `GET /api/runner` — hub-free, so it resolves
 * even when the fleet-summary forward fails, letting the strip render its degraded
 * state while the rest of the panel stays lit. */
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

/** Render `LocalInfo` with `/api/runner` resolved and `/api/fleet-summary` answered by
 * `fleetSummary` — a canned counts body, or a {@link stubError} for the degraded path. */
async function render(fleetSummary: () => unknown) {
  const stub = stubRequestClient(runnerClient, (method, path) => {
    if (method !== 'GET') return {};
    if (path === '/api/runner') return RUNNER_STATUS;
    if (path === '/api/fleet-summary') return fleetSummary();
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

  it('reads the counts off GET /api/fleet-summary and renders the four buckets', async () => {
    const { fixture, stub: s } = await render(() => COUNTS);
    stub = s;
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="fleet-ready"]')?.textContent).toContain('4');
    expect(el.querySelector('[data-testid="fleet-running"]')?.textContent).toContain('3');
    expect(el.querySelector('[data-testid="fleet-waiting"]')?.textContent).toContain('2');
    expect(el.querySelector('[data-testid="fleet-needs"]')?.textContent).toContain('1');
    // Live, not degraded.
    expect(el.querySelector('[data-testid="fleet-strip"]')?.classList.contains('stale')).toBe(false);
    expect(el.querySelector('[data-testid="fleet-age"]')?.textContent).toContain('live');
    // It read through the runner's own pass-through, not the hub directly.
    expect(stub.forRoute('/api/fleet-summary', 'GET').length).toBeGreaterThan(0);
  });

  it('renders a zero count, not a dash, for an empty bucket', async () => {
    const { fixture, stub: s } = await render(() => ({ ready: 0, running: 0, waiting: 0, needs: 0 }));
    stub = s;
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="fleet-ready"]')?.textContent).toContain('0');
    expect(el.querySelector('[data-testid="fleet-ready"]')?.textContent).not.toContain('—');
  });

  it('degrades to the last-known/dimmed state when the hub forward fails', async () => {
    // A hub outage surfaces as the proxy's 502 — the strip dims and banners "last known",
    // and the rest of the panel (hub-free) is unaffected.
    const { fixture, stub: s } = await render(() => stubError(502, { detail: 'hub unreachable' }));
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

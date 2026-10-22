import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { FactLog } from './fact-log';

let stub: RequestClientStub | undefined;

afterEach(() => stub?.restore());

/** A serving runner on the host, as `GET /api/host/runners` lists it. */
function hostRunner(runnerId: string, name: string): runnerApi.HostRunnerView {
  return {
    runner_id: runnerId,
    name,
    state: 'serving',
    served: true,
    halted_reasons: [],
    pause: { local: false, hub: false, effective: false, local_reason: null },
    throttle: null,
    bindings: [{ project: 'p', workspace_id: 'ws', agents: 4 }],
    agents_in_use: 0,
    max_agents: 4,
  };
}

/** A full `DashboardView` body, `facts.items` set to `facts` and every other
 * section its empty default — `FactLog` reads off each runner's shared
 * `/runners/<id>/api/dashboard` poll, not a `/api/facts` route of its own. */
function dashboardBody(facts: readonly runnerApi.FactView[]): runnerApi.DashboardView {
  return {
    runner: {
      runner_id: 'runner-local',
      workspace_id: 'workspace-local',
      pause: { local: false, hub: false, effective: false, local_reason: null },
      throttle: null,
      capacities: { used: 0, agents: 4, free: 4, bindings: [{ project: 'p', project_id: 'p', workspace_id: 'ws', agents: 4, used: 0, free: 4 }] },
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
    fleet_summary: null,
    facts: { items: [...facts] },
    harness_health: { items: [] },
  };
}

/** Render `FactLog` over a host serving `rn_1` (`r1`) with `facts` in its ledger, and — given
 * `otherFacts` — `rn_2` (`r2`) with those in its. */
async function render(
  facts: readonly runnerApi.FactView[],
  otherFacts?: readonly runnerApi.FactView[],
): Promise<{ el: HTMLElement; fixture: ComponentFixture<FactLog> }> {
  const runners = [hostRunner('rn_1', 'r1'), ...(otherFacts ? [hostRunner('rn_2', 'r2')] : [])];
  stub = stubRequestClient(runnerClient, (method, path) => {
    if (method !== 'GET') return {};
    if (path === '/api/host/runners') return { items: runners };
    if (path === '/api/host/environments') return { workspaces: [] };
    if (path === '/runners/rn_1/api/dashboard') return dashboardBody(facts);
    if (path === '/runners/rn_2/api/dashboard') return dashboardBody(otherFacts ?? []);
    if (/^\/runners\/[^/]+\/api\/leases$/.test(path)) return { items: [] };
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [FactLog],
    providers: [
      provideZonelessChangeDetection(),
      provideRouter([]),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(FactLog);
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture };
}

/**
 * `FactLog`'s own concern is the merged {@link injectPanelFacts} read and
 * handing its facts to the view (`bzh:frontend-container-presentational`)
 * — the day/time formatting itself is `FactLogView`'s, plain-input covered in
 * `fact-log-view.spec.ts` with no query stub required. This spec keeps one
 * rendering case as pass-through proof that the query's resolved facts actually
 * reach the view.
 */
describe('FactLog', () => {
  // Pin both the zone and "now" so the local-day boundary is deterministic —
  // a bare wall-clock read would make this flaky in CI.
  beforeEach(() => {
    vi.stubEnv('TZ', 'America/New_York');
    vi.setSystemTime(new Date('2026-07-16T15:00:00.000Z')); // 11:00 EDT
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
  });

  it("hands the dashboard query's resolved facts to the view", async () => {
    const { el } = await render([
      {
        seq: 1,
        kind: 'chunk_claimed',
        created_at: '2026-07-01T11:00:00+00:00',
        chunk_id: null,
        lease_id: null,
        acked_at: null,
        project_id: null,
      },
    ]);

    const row = el.querySelector('[data-testid="fact-row"]');
    expect(row?.querySelector('.t .day')?.textContent).toBe('2026-07-01');
    expect(row?.querySelector('.t .time')?.textContent).toBe('07:00:00');
  });

  it('merges every serving runner’s facts newest first, each naming its runner', async () => {
    const fact = { kind: 'chunk_claimed', chunk_id: null, lease_id: null, acked_at: null, project_id: null };
    const { el } = await render(
      [{ ...fact, seq: 1, created_at: '2026-07-16T11:00:00+00:00' }],
      [{ ...fact, seq: 1, created_at: '2026-07-16T12:00:00+00:00' }],
    );

    const rows = Array.from(el.querySelectorAll('[data-testid="fact-row"]'));
    expect(rows.map((row) => row.getAttribute('data-runner-id'))).toEqual(['rn_2', 'rn_1']);
    expect(rows.map((row) => row.querySelector('[data-testid="fact-runner"]')?.textContent)).toEqual(['r2', 'r1']);
  });
});

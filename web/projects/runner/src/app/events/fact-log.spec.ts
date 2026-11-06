import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type runnerApi } from 'fleet';
import { type RequestClientStub, hostRunnerView, runnerDashboardView, settle, stubRunnerClient } from 'fleet/testing';
import { vi } from 'vitest';

import { FactLog } from './fact-log';

let stub: RequestClientStub | undefined;

afterEach(() => stub?.restore());

/** A serving runner on the host, as `GET /api/host/runners` lists it. */
function hostRunner(runnerId: string, name: string): runnerApi.HostRunnerView {
  return hostRunnerView({ runner_id: runnerId, name, bindings: [{ project: 'p', workspace_id: 'ws', agents: 4 }], max_agents: 4 });
}

/** A full `DashboardView` body, `facts.items` set to `facts` and every other
 * section its empty default — `FactLog` reads off each runner's shared
 * `/runners/<id>/api/dashboard` poll, not a `/api/facts` route of its own. */
function dashboardBody(facts: readonly runnerApi.FactView[]): runnerApi.DashboardView {
  return runnerDashboardView({ facts: { items: [...facts] } });
}

/** Render `FactLog` over a host serving `rn_1` (`r1`) with `facts` in its ledger, and — given
 * `otherFacts` — `rn_2` (`r2`) with those in its. */
async function render(
  facts: readonly runnerApi.FactView[],
  otherFacts?: readonly runnerApi.FactView[],
): Promise<{ el: HTMLElement; fixture: ComponentFixture<FactLog> }> {
  const runners = [hostRunner('rn_1', 'r1'), ...(otherFacts ? [hostRunner('rn_2', 'r2')] : [])];
  stub = stubRunnerClient({
    'GET /api/host/runners': () => ({ items: runners }),
    'GET /api/host/environments': () => ({ workspaces: [] }),
    'GET /runners/{runner_id}/api/dashboard': (_request, { runner_id }) =>
      dashboardBody(runner_id === 'rn_1' ? facts : (otherFacts ?? [])),
    'GET /runners/{runner_id}/api/leases': () => ({ items: [] }),
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

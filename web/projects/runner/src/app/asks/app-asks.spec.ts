import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { LocalAsks } from './app-asks';

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

/** A full `DashboardView` body, `asks.items` set to `asks` and every other section
 * its empty default — `LocalAsks` reads off each runner's shared `/runners/<id>/api/dashboard`
 * poll, not a `/api/asks` route of its own. */
function dashboardBody(asks: readonly runnerApi.AskView[]): runnerApi.DashboardView {
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
    asks: { items: [...asks] },
    escalations: { items: [] },
    takeovers: { items: [] },
    fleet_summary: null,
    facts: { items: [] },
    harness_health: { items: [] },
  };
}

/** Render `LocalAsks` over a host serving `rn_1` (`r1`) with `asks` open on it, and — given
 * `otherAsks` — `rn_2` (`r2`) with those open on it. */
async function render(
  asks: readonly runnerApi.AskView[],
  otherAsks?: readonly runnerApi.AskView[],
): Promise<{ el: HTMLElement; fixture: ComponentFixture<LocalAsks> }> {
  const runners = [hostRunner('rn_1', 'r1'), ...(otherAsks ? [hostRunner('rn_2', 'r2')] : [])];
  stub = stubRequestClient(runnerClient, (method, path) => {
    if (method !== 'GET') return {};
    if (path === '/api/host/runners') return { items: runners };
    if (path === '/api/host/environments') return { workspaces: [] };
    if (path === '/runners/rn_1/api/dashboard') return dashboardBody(asks);
    if (path === '/runners/rn_2/api/dashboard') return dashboardBody(otherAsks ?? []);
    if (/^\/runners\/[^/]+\/api\/leases$/.test(path)) return { items: [] };
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [LocalAsks],
    providers: [
      provideZonelessChangeDetection(),
      provideRouter([]),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalAsks);
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture };
}

const ASK: runnerApi.AskView = {
  question_id: 'q-1',
  chunk_id: 'ch_01ABC',
  lease_id: 'lease-1',
  session_id: null,
  question: 'Proceed with the migration?',
  asked_at: '2026-07-16T11:59:30.000Z',
  harness_id: null,
  options: [],
  project_id: null,
};

describe('LocalAsks', () => {
  it('renders an open ask with its chunk ref and question text', async () => {
    const { el } = await render([ASK]);

    const row = el.querySelector('[data-testid="ask-row"]');
    expect(row?.getAttribute('data-question-id')).toBe('q-1');
    expect(row?.querySelector('.q')?.textContent).toBe('Proceed with the migration?');
    expect(row?.querySelector('.chunk')?.textContent?.trim().length).toBeGreaterThan(0);
  });

  it('merges every serving runner’s open asks, each row naming the runner it is open on', async () => {
    const { el } = await render([ASK], [{ ...ASK, question_id: 'q-2', question: 'Ship it?' }]);

    const rows = Array.from(el.querySelectorAll('[data-testid="ask-row"]'));
    expect(rows.map((row) => row.getAttribute('data-question-id'))).toEqual(['q-1', 'q-2']);
    expect(rows.map((row) => row.querySelector('[data-testid="ask-runner"]')?.textContent)).toEqual(['r1', 'r2']);
  });

  it('renders the empty state when there are no open asks', async () => {
    const { el } = await render([]);

    expect(el.querySelector('[data-testid="asks-empty"]')).not.toBeNull();
  });

  describe('asked-for ticking', () => {
    afterEach(() => vi.restoreAllMocks());

    // `shouldAdvanceTime` keeps settle()'s macrotask resolvable while the ticking
    // interval is driven by advanceTimersByTimeAsync — the wait is a jump, not a
    // real second spent in the gating job.
    it('re-renders asked-for at least once a second with no new data', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        const dateNow = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-07-16T12:00:00.000Z'));
        const { fixture } = await render([ASK]);
        const el = fixture.nativeElement as HTMLElement;
        expect(el.querySelector('.asked')?.textContent).toContain('30s');

        dateNow.mockReturnValue(Date.parse('2026-07-16T12:01:00.000Z'));
        await vi.advanceTimersByTimeAsync(1_100);
        fixture.detectChanges();

        expect(el.querySelector('.asked')?.textContent).toContain('1m');
      } finally {
        vi.useRealTimers();
      }
    });
  });
});

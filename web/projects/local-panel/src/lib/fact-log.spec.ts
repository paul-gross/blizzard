import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { FactLog } from './fact-log';

let stub: RequestClientStub | undefined;

afterEach(() => stub?.restore());

/** A full `DashboardView` body, `facts.items` set to `facts` and every other
 * section its empty default — `FactLog` reads off the shared `/api/dashboard`
 * poll (issue #311), not a `/api/facts` route of its own. */
function dashboardBody(facts: readonly runnerApi.FactView[]): runnerApi.DashboardView {
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
    fleet_summary: null,
    facts: { items: [...facts] },
  };
}

async function render(facts: readonly runnerApi.FactView[]): Promise<{ el: HTMLElement; fixture: ComponentFixture<FactLog> }> {
  stub = stubRequestClient(runnerClient, (method, path) => (method === 'GET' && path === '/api/dashboard' ? dashboardBody(facts) : {}));
  await TestBed.configureTestingModule({
    imports: [FactLog],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(FactLog);
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture };
}

describe('FactLog', () => {
  describe('fact timestamps render in browser-local time (issue #136)', () => {
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

    it("renders today's fact as the local time alone, no day line", async () => {
      const { el } = await render([
        {
          seq: 1,
          kind: 'chunk_claimed',
          created_at: '2026-07-16T11:00:00+00:00', // 07:00 EDT, same local day as "now"
          chunk_id: null,
          lease_id: null,
          acked_at: null,
        },
      ]);

      const row = el.querySelector('[data-testid="fact-row"]');
      expect(row?.querySelector('.t .day')).toBeNull();
      expect(row?.querySelector('.t .time')?.textContent).toBe('07:00:00');
      expect(row?.querySelector('.t')?.getAttribute('title')).toBe('2026/07/16 07:00:00');
    });

    it("renders yesterday's fact as \"Yesterday\" above the local time", async () => {
      const { el } = await render([
        {
          seq: 1,
          kind: 'chunk_claimed',
          created_at: '2026-07-15T23:30:00+00:00', // 19:30 EDT the day before "now"
          chunk_id: null,
          lease_id: null,
          acked_at: null,
        },
      ]);

      const row = el.querySelector('[data-testid="fact-row"]');
      expect(row?.querySelector('.t .day')?.textContent).toBe('Yesterday');
      expect(row?.querySelector('.t .time')?.textContent).toBe('19:30:00');
    });

    it('renders an older fact with its yyyy-mm-dd date above the local time', async () => {
      const { el } = await render([
        {
          seq: 1,
          kind: 'chunk_claimed',
          created_at: '2026-07-01T11:00:00+00:00',
          chunk_id: null,
          lease_id: null,
          acked_at: null,
        },
      ]);

      const row = el.querySelector('[data-testid="fact-row"]');
      expect(row?.querySelector('.t .day')?.textContent).toBe('2026-07-01');
      expect(row?.querySelector('.t .time')?.textContent).toBe('07:00:00');
    });
  });
});

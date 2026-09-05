import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { EnvList } from './env-list';

let stub: RequestClientStub | undefined;

afterEach(() => stub?.restore());

/** A full `DashboardView` body, `environments.items` set to `envs` and every other
 * section its empty default — `EnvList` reads off the shared `/api/dashboard` poll
 * (issue #311), not a `/api/environments` route of its own. */
function dashboardBody(envs: readonly runnerApi.EnvironmentView[]): runnerApi.DashboardView {
  return {
    runner: {
      runner_id: 'runner-local',
      workspace_id: 'workspace-local',
      pause: { local: false, hub: false, effective: false },
      capacities: { max_agents: 4, used: 0, free: 4 },
      hub: { endpoint: 'http://127.0.0.1:8421', reachable: true, last_contact_at: null, buffer_depth: 0 },
      last_tick_at: null,
    },
    environments: { items: [...envs] },
    asks: { items: [] },
    escalations: { items: [] },
    takeovers: { items: [] },
    fleet_summary: null,
    facts: { items: [] },
  };
}

async function render(envs: readonly runnerApi.EnvironmentView[]): Promise<{ el: HTMLElement; fixture: ComponentFixture<EnvList> }> {
  stub = stubRequestClient(runnerClient, (method, path) => (method === 'GET' && path === '/api/dashboard' ? dashboardBody(envs) : {}));
  await TestBed.configureTestingModule({
    imports: [EnvList],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(EnvList);
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture };
}

describe('EnvList', () => {
  it('renders a held row with its chunk ref and how long it has been held', async () => {
    const { el } = await render([{ environment_id: 'r2', chunk_id: 'ch_01ABC', held_since: '2026-07-16T11:59:30.000Z' }]);

    const row = el.querySelector('[data-testid="env-row"]');
    expect(row?.getAttribute('data-held')).toBe('true');
    expect(row?.querySelector('.env')?.textContent).toBe('r2');
    expect(row?.querySelector('.chunk')?.textContent?.trim().length).toBeGreaterThan(0);
    expect(row?.querySelector('[data-testid="env-held-for"]')?.textContent?.trim().length).toBeGreaterThan(0);
  });

  it('renders an unused row with no chunk ref and no held-for text', async () => {
    const { el } = await render([{ environment_id: 'alpha', chunk_id: null, held_since: null }]);

    const row = el.querySelector('[data-testid="env-row"]');
    expect(row?.getAttribute('data-held')).toBe('false');
    expect(row?.querySelector('.chunk')?.textContent).toBe('');
    expect(row?.querySelector('[data-testid="env-held-for"]')?.textContent).toBe('');
  });

  it('renders the empty state only when the pool itself is empty', async () => {
    const { el } = await render([]);

    expect(el.querySelector('[data-testid="env-empty"]')).not.toBeNull();
  });

  describe('held-for ticking', () => {
    afterEach(() => vi.restoreAllMocks());

    // `shouldAdvanceTime` keeps settle()'s macrotask resolvable while the ticking
    // interval is driven by advanceTimersByTimeAsync — the wait is a jump, not a
    // real second spent in the gating job.
    it('re-renders held-for at least once a second with no new data', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        const dateNow = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-07-16T12:00:00.000Z'));
        const { fixture } = await render([
          { environment_id: 'r2', chunk_id: 'ch_01ABC', held_since: '2026-07-16T11:59:30.000Z' },
        ]);
        const el = fixture.nativeElement as HTMLElement;
        expect(el.querySelector('[data-testid="env-held-for"]')?.textContent).toContain('30s');

        dateNow.mockReturnValue(Date.parse('2026-07-16T12:01:00.000Z'));
        await vi.advanceTimersByTimeAsync(1_100);
        fixture.detectChanges();

        expect(el.querySelector('[data-testid="env-held-for"]')?.textContent).toContain('1m');
      } finally {
        vi.useRealTimers();
      }
    });
  });
});

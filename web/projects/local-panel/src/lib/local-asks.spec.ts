import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { LocalAsks } from './local-asks';

let stub: RequestClientStub | undefined;

afterEach(() => stub?.restore());

/** A full `DashboardView` body, `asks.items` set to `asks` and every other section
 * its empty default — `LocalAsks` reads off the shared `/api/dashboard` poll
 * (issue #311), not a `/api/asks` route of its own. */
function dashboardBody(asks: readonly runnerApi.AskView[]): runnerApi.DashboardView {
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
    asks: { items: [...asks] },
    escalations: { items: [] },
    takeovers: { items: [] },
    fleet_summary: null,
    facts: { items: [] },
  };
}

async function render(asks: readonly runnerApi.AskView[]): Promise<{ el: HTMLElement; fixture: ComponentFixture<LocalAsks> }> {
  stub = stubRequestClient(runnerClient, (method, path) => (method === 'GET' && path === '/api/dashboard' ? dashboardBody(asks) : {}));
  await TestBed.configureTestingModule({
    imports: [LocalAsks],
    providers: [
      provideZonelessChangeDetection(),
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
};

describe('LocalAsks', () => {
  it('renders an open ask with its chunk ref and question text', async () => {
    const { el } = await render([ASK]);

    const row = el.querySelector('[data-testid="ask-row"]');
    expect(row?.getAttribute('data-question-id')).toBe('q-1');
    expect(row?.querySelector('.q')?.textContent).toBe('Proceed with the migration?');
    expect(row?.querySelector('.chunk')?.textContent?.trim().length).toBeGreaterThan(0);
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

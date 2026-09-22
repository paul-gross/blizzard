import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { LocalSubscriptions } from './local-subscriptions';

let stub: RequestClientStub | undefined;

afterEach(() => stub?.restore());

/** A full `DashboardView` body, `subscriptions.items` set to `subscriptions` and every
 * other section its empty default — `LocalSubscriptions` reads off the shared
 * `/api/dashboard` poll (issue #311), not a `/api/subscriptions` route of its own. */
function dashboardBody(subscriptions: readonly runnerApi.SubscriptionView[]): runnerApi.DashboardView {
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
    facts: { items: [] },
    harness_health: { items: [] },
    subscriptions: { items: [...subscriptions] },
  };
}

async function render(
  subscriptions: readonly runnerApi.SubscriptionView[],
): Promise<{ el: HTMLElement; fixture: ComponentFixture<LocalSubscriptions> }> {
  stub = stubRequestClient(runnerClient, (method, path) =>
    method === 'GET' && path === '/api/dashboard' ? dashboardBody(subscriptions) : {},
  );
  await TestBed.configureTestingModule({
    imports: [LocalSubscriptions],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalSubscriptions);
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture };
}

const NEVER_SAMPLED: runnerApi.SubscriptionView = {
  slug: 'anthropic',
  name: 'Anthropic',
  provider: 'anthropic',
  sampled_at: null,
  ok: null,
  miss_reason: null,
};

const OK: runnerApi.SubscriptionView = {
  slug: 'anthropic',
  name: 'Anthropic',
  provider: 'anthropic',
  sampled_at: '2026-07-16T11:59:30.000Z',
  ok: true,
  miss_reason: null,
};

const MISS: runnerApi.SubscriptionView = {
  slug: 'codex',
  name: 'Codex',
  provider: 'openai',
  sampled_at: '2026-07-16T11:58:00.000Z',
  ok: false,
  miss_reason: 'credential_lapsed',
};

describe('LocalSubscriptions', () => {
  it('renders a never-sampled subscription', async () => {
    const { el } = await render([NEVER_SAMPLED]);

    const row = el.querySelector('[data-testid="subscription-row"]');
    expect(row?.getAttribute('data-slug')).toBe('anthropic');
    expect(row?.querySelector('.condition')?.textContent).toBe('never sampled');
  });

  it('renders the empty state when there are no declared subscriptions', async () => {
    const { el } = await render([]);

    expect(el.querySelector('[data-testid="subscriptions-empty"]')).not.toBeNull();
  });

  it('renders a miss subscription with its reason, distinctly from an ok one', async () => {
    const { el } = await render([OK, MISS]);

    const rows = el.querySelectorAll('[data-testid="subscription-row"]');
    expect(rows).toHaveLength(2);
    const missRow = Array.from(rows).find((row) => row.getAttribute('data-slug') === 'codex');
    expect(missRow?.querySelector('.condition')?.textContent).toBe('miss: credential_lapsed');
    expect(missRow?.classList.contains('miss')).toBe(true);
  });

  describe('sampled-ago ticking', () => {
    afterEach(() => vi.restoreAllMocks());

    it('re-renders sampled-ago at least once a second with no new data', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        const dateNow = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-07-16T12:00:00.000Z'));
        const { fixture } = await render([OK]);
        const el = fixture.nativeElement as HTMLElement;
        expect(el.querySelector('.sampled')?.textContent).toContain('30s');

        dateNow.mockReturnValue(Date.parse('2026-07-16T12:01:00.000Z'));
        await vi.advanceTimersByTimeAsync(1_100);
        fixture.detectChanges();

        expect(el.querySelector('.sampled')?.textContent).toContain('1m');
      } finally {
        vi.useRealTimers();
      }
    });
  });
});

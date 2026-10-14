import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { HostSubscriptions } from './host-subscriptions';

let stub: RequestClientStub | undefined;

afterEach(() => stub?.restore());

/** The host's runner list the spender names resolve against. */
const RUNNERS = { items: [{ runner_id: 'rn_1', name: 'r-claude', state: 'serving' }] };

async function render(
  subscriptions: readonly runnerApi.HostSubscriptionView[],
): Promise<{ el: HTMLElement; fixture: ComponentFixture<HostSubscriptions> }> {
  stub = stubRequestClient(runnerClient, (method, path) => {
    if (method !== 'GET') return {};
    if (path === '/api/host/subscriptions') return { items: [...subscriptions] };
    if (path === '/api/host/runners') return RUNNERS;
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [HostSubscriptions],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(HostSubscriptions);
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture };
}

const NEVER_SAMPLED: runnerApi.HostSubscriptionView = {
  slug: 'anthropic',
  name: 'Anthropic',
  provider: 'anthropic',
  sampled_at: null,
  ok: null,
  miss_reason: null,
};

const OK: runnerApi.HostSubscriptionView = {
  slug: 'anthropic',
  name: 'Anthropic',
  provider: 'anthropic',
  sampled_at: '2026-07-16T11:59:30.000Z',
  ok: true,
  miss_reason: null,
};

const MISS: runnerApi.HostSubscriptionView = {
  slug: 'codex',
  name: 'Codex',
  provider: 'openai',
  sampled_at: '2026-07-16T11:58:00.000Z',
  ok: false,
  miss_reason: 'credential_lapsed',
};

const RENEWED: runnerApi.HostSubscriptionView = {
  slug: 'codex',
  name: 'Codex',
  provider: 'openai',
  sampled_at: '2026-07-16T11:59:30.000Z',
  ok: true,
  miss_reason: null,
  renewal_attempted_at: '2026-07-16T11:59:00.000Z',
  renewal_result: 'renewed',
  renewal_failure_reason: null,
};

describe('HostSubscriptions', () => {
  it('names the runners spending a subscription', async () => {
    const { el } = await render([{ ...OK, spenders: ['rn_1', 'rn_9'] }]);

    expect(el.querySelector('[data-testid="subscription-pace-group-spenders"]')?.textContent?.trim()).toBe(
      'spent by r-claude, rn_9',
    );
  });

  it('renders a never-sampled subscription', async () => {
    const { el } = await render([NEVER_SAMPLED]);

    const row = el.querySelector('[data-testid="subscription-pace-group"]');
    expect(row?.getAttribute('data-subscription-slug')).toBe('anthropic');
    expect(row?.querySelector('[data-testid="subscription-pace-group-condition"]')?.textContent?.trim()).toBe('never sampled');
  });

  it('renders the empty state when there are no declared subscriptions', async () => {
    const { el } = await render([]);

    expect(el.querySelector('[data-testid="subscriptions-empty"]')).not.toBeNull();
  });

  it('renders a miss subscription with its reason, distinctly from an ok one', async () => {
    const { el } = await render([OK, MISS]);

    const rows = el.querySelectorAll('[data-testid="subscription-pace-group"]');
    expect(rows).toHaveLength(2);
    const missRow = Array.from(rows).find((row) => row.getAttribute('data-subscription-slug') === 'codex');
    expect(missRow?.querySelector('[data-testid="subscription-pace-group-condition"]')?.textContent?.trim()).toBe('miss: credential lapsed: log in again');
    expect(missRow?.querySelector('[data-testid="subscription-pace-group-condition"]')?.classList.contains('miss')).toBe(true);
  });

  it('renders a recorded renewal outcome alongside the attempt', async () => {
    const { el } = await render([RENEWED]);

    const row = el.querySelector('[data-testid="subscription-pace-group"]');
    expect(row?.querySelector('[data-testid="subscription-pace-group-renewal"]')?.textContent).toMatch(/^renewal: renewed /);
  });

  it('renders no renewal line for a subscription never renewed', async () => {
    const { el } = await render([OK]);

    const row = el.querySelector('[data-testid="subscription-pace-group"]');
    expect(row?.querySelector('[data-testid="subscription-pace-group-renewal"]')).toBeNull();
  });

  describe('sampled-ago ticking', () => {
    afterEach(() => vi.restoreAllMocks());

    it('re-renders sampled-ago at least once a second with no new data', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        const dateNow = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-07-16T12:00:00.000Z'));
        const { fixture } = await render([OK]);
        const el = fixture.nativeElement as HTMLElement;
        expect(el.querySelector('[data-testid="subscription-pace-group-sampled"]')?.textContent).toContain('30s');

        dateNow.mockReturnValue(Date.parse('2026-07-16T12:01:00.000Z'));
        await vi.advanceTimersByTimeAsync(1_100);
        fixture.detectChanges();

        expect(el.querySelector('[data-testid="subscription-pace-group-sampled"]')?.textContent).toContain('1m');
      } finally {
        vi.useRealTimers();
      }
    });
  });
});

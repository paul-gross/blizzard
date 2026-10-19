import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { MobileTabBar } from './mobile-tab-bar';

/** Two serving runners on the host. */
const RUNNERS = {
  items: ['rn_1', 'rn_2'].map((runnerId, i) => ({
    runner_id: runnerId,
    name: `r${i + 1}`,
    state: 'serving',
    served: true,
    halted_reasons: [],
    pause: { local: false, hub: false, effective: false, local_reason: null },
    bindings: [{ project: 'p', workspace_id: 'ws', agents: 4 }],
    agents_in_use: 0,
    max_agents: 4,
  })),
};

describe('MobileTabBar', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function render(asks: readonly unknown[]) {
    stub = stubRequestClient(runnerClient, (method, path) => {
      if (method !== 'GET') return {};
      if (path === '/api/host/runners') return RUNNERS;
      // Every covered runner's asks count toward the badge.
      if (path === '/runners/rn_1/api/dashboard') return { asks: { items: asks.slice(0, 1) } };
      if (path === '/runners/rn_2/api/dashboard') return { asks: { items: asks.slice(1) } };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [MobileTabBar],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(MobileTabBar);
    await settle(fixture);
    return fixture;
  }

  it('renders Board, Events, and Host routed to their flat pages, with Asks inert', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="tab-board"]')?.getAttribute('href')).toBe('/board');
    expect(el.querySelector('[data-testid="tab-events"]')?.getAttribute('href')).toBe('/events');
    expect(el.querySelector('[data-testid="tab-host"]')?.getAttribute('href')).toBe('/host');
    expect(el.querySelector('[data-testid="tab-machine"]')).toBeNull();
    const asks = el.querySelector('[data-testid="tab-asks-runner"]');
    expect(asks?.textContent).toContain('Asks');
    expect(asks?.hasAttribute('disabled')).toBe(true);
    expect(asks?.classList.contains('inert')).toBe(true);
  });

  it('omits the Asks badge when there are no open asks', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="tab-asks-runner-badge"]')).toBeNull();
  });

  it('shows the open-asks count across every covered runner on the Asks tab', async () => {
    const fixture = await render([
      { question_id: 'qn_1', chunk_id: 'ch_1', lease_id: 'lease_1', question: 'a?', options: [], session_id: null, asked_at: null },
      { question_id: 'qn_2', chunk_id: 'ch_2', lease_id: 'lease_2', question: 'b?', options: [], session_id: null, asked_at: null },
    ]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="tab-asks-runner-badge"]')?.textContent).toBe('2');
  });
});

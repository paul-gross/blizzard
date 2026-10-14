import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { HostRunners } from './host-runners';

describe('HostRunners', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function render(runners: readonly unknown[]): Promise<HTMLElement> {
    stub = stubRequestClient(runnerClient, (method, path) =>
      method === 'GET' && path === '/api/host/runners' ? { items: runners } : {},
    );
    await TestBed.configureTestingModule({
      imports: [HostRunners],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(HostRunners);
    await settle(fixture);
    return fixture.nativeElement as HTMLElement;
  }

  it('links a serving runner to its board, with its brake, agents, and bindings', async () => {
    const el = await render([
      {
        runner_id: 'rn_1',
        name: 'r-claude',
        state: 'serving',
        pause: { local: false, hub: true, effective: true },
        agents_in_use: 1,
        max_agents: 2,
        bindings: [{ project: 'blizzard', workspace_id: 'ws-a', agents: 2 }],
      },
    ]);

    const row = el.querySelector('[data-testid="host-runner-row"]');
    expect(row?.querySelector('[data-testid="host-runner-link"]')?.getAttribute('href')).toBe('/runners/rn_1/board');
    expect(row?.querySelector('[data-testid="host-runner-brake"]')?.textContent?.trim()).toBe('paused by hub');
    expect(row?.querySelector('[data-testid="host-runner-agents"]')?.textContent?.trim()).toBe('1/2 agents');
    expect(row?.querySelector('[data-testid="host-runner-binding"]')?.textContent?.trim()).toBe('blizzard · ws-a · 2 agents');
  });

  it('lists a halted runner with its reasons and no link', async () => {
    const el = await render([{ runner_id: null, name: 'r-broken', state: 'halted', halted_reasons: ['no hub token'] }]);

    const row = el.querySelector('[data-testid="host-runner-row"]');
    expect(row?.querySelector('[data-testid="host-runner-link"]')).toBeNull();
    expect(row?.querySelector('[data-testid="host-runner-halted"]')).not.toBeNull();
    expect(row?.querySelector('[data-testid="host-runner-halted-reason"]')?.textContent?.trim()).toBe('no hub token');
  });

  it('renders the empty state for a host with no runners', async () => {
    const el = await render([]);

    expect(el.querySelector('[data-testid="host-runners-empty"]')).not.toBeNull();
  });
});

import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { HubPanel } from './hub-panel';

function hostRunner(runnerId: string, name: string): runnerApi.HostRunnerView {
  return {
    runner_id: runnerId,
    name,
    state: 'serving',
    served: true,
    halted_reasons: [],
    pause: { local: false, hub: false, effective: false, local_reason: null },
    bindings: [{ project: 'p', workspace_id: 'ws', agents: 4 }],
    agents_in_use: 0,
    max_agents: 4,
  };
}

function dashboard(hub: Partial<runnerApi.DashboardView['runner']['hub']>, fleet: runnerApi.FleetSummaryView | null): unknown {
  return {
    fleet_summary: fleet,
    runner: {
      runner_id: 'rn',
      workspace_id: 'ws',
      pause: { local: false, hub: false, effective: false, local_reason: null },
      capacities: { used: 0, bindings: [] },
      hub: { endpoint: 'http://127.0.0.1:4522', reachable: true, last_contact_at: '2026-10-19T19:27:08.227Z', buffer_depth: 0, ...hub },
      last_tick_at: null,
    },
  };
}

const FLEET: runnerApi.FleetSummaryView = { ready: 3, running: 5, waiting: 1, needs: 2 };

let stub: RequestClientStub;

afterEach(() => stub.restore());

/** Mounts the hub panel over the host's runners, answering each runner's dashboard off `dashboards` (by runner id),
 * read afresh on every request so a refetch sees a swapped answer. */
async function render(
  runners: readonly runnerApi.HostRunnerView[],
  dashboards: Record<string, unknown>,
): Promise<{ fixture: ComponentFixture<HubPanel>; el: HTMLElement }> {
  stub = stubRequestClient(runnerClient, (method, path) => {
    if (method !== 'GET') return {};
    if (path === '/api/host/runners') return { items: runners };
    const match = /^\/runners\/([^/]+)\/api\/dashboard$/.exec(path);
    return match ? dashboards[match[1]] : {};
  });
  await TestBed.configureTestingModule({
    imports: [HubPanel],
    providers: [
      provideZonelessChangeDetection(),
      provideRouter([]),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(HubPanel);
  await settle(fixture);
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

const text = (el: HTMLElement, testid: string): string | undefined =>
  el.querySelector(`[data-testid="${testid}"]`)?.textContent?.replace(/\s+/g, ' ').trim();

describe('HubPanel', () => {
  it('reads CONNECTED, links the hub board, and shows the fleet counts while every runner reaches the hub', async () => {
    const { el } = await render([hostRunner('rn_1', 'r1'), hostRunner('rn_2', 'r2')], {
      rn_1: dashboard({}, FLEET),
      rn_2: dashboard({}, FLEET),
    });

    expect(text(el, 'hub-link')).toBe('CONNECTED');
    expect(el.querySelector('[data-testid="hub-link"]')?.classList).toContain('up');
    expect(el.querySelector('[data-testid="board-link"]')?.getAttribute('href')).toBe('http://127.0.0.1:4522');
    expect(el.querySelector('[data-testid="hub-issue"]')).toBeNull();
    expect(el.querySelector('[data-testid="hub-buffered"]')).toBeNull();
    expect(text(el, 'fleet-ready')).toBe('3ready');
    expect(text(el, 'fleet-running')).toBe('5running');
    expect(text(el, 'fleet-waiting')).toBe('1waiting');
    expect(text(el, 'fleet-needs')).toBe('2needs');
    expect(el.querySelector('[data-testid="fleet-strip"]')?.classList).not.toContain('stale');
  });

  it('counts the runners connected and names the one that cannot reach the hub', async () => {
    const { el } = await render([hostRunner('rn_1', 'r1'), hostRunner('rn_2', 'r2')], {
      rn_1: dashboard({ buffer_depth: 2 }, FLEET),
      rn_2: dashboard({ reachable: false, buffer_depth: 5 }, null),
    });

    expect(text(el, 'hub-link')).toBe('1 / 2 CONNECTED');
    expect(el.querySelector('[data-testid="hub-link"]')?.classList).toContain('part');
    expect(text(el, 'hub-buffered')).toBe('· 7 buffered');
    const issues = Array.from(el.querySelectorAll('[data-testid="hub-issue"]')).map((issue) => issue.textContent?.replace(/\s+/g, ' ').trim());
    expect(issues).toEqual(['r2 cannot reach the hub']);
  });

  it('reads UNREACHABLE once no runner reaches the hub', async () => {
    const { el } = await render([hostRunner('rn_1', 'r1')], { rn_1: dashboard({ reachable: false }, null) });

    expect(text(el, 'hub-link')).toBe('UNREACHABLE');
    expect(el.querySelector('[data-testid="hub-link"]')?.classList).toContain('down');
  });

  it('dims the fleet strip to its last-known counts once the hub stops answering', async () => {
    const dashboards: Record<string, unknown> = { rn_1: dashboard({}, FLEET) };
    const { fixture, el } = await render([hostRunner('rn_1', 'r1')], dashboards);
    expect(text(el, 'fleet-running')).toBe('5running');

    dashboards['rn_1'] = dashboard({}, null);
    await TestBed.inject(QueryClient).refetchQueries();
    await settle(fixture);

    expect(el.querySelector('[data-testid="fleet-strip"]')?.classList).toContain('stale');
    expect(text(el, 'fleet-running')).toBe('5running');
  });

  it('shows dashes in the fleet strip before the hub has ever answered', async () => {
    const { el } = await render([hostRunner('rn_1', 'r1')], { rn_1: dashboard({}, null) });

    expect(text(el, 'fleet-ready')).toBe('—ready');
    expect(el.querySelector('[data-testid="fleet-strip"]')?.classList).toContain('stale');
  });

  it('says no runner reports a hub when the host serves none', async () => {
    const { el } = await render([], {});

    expect(text(el, 'hub-panel-empty')).toBe('NO RUNNER REPORTS A HUB');
  });
});

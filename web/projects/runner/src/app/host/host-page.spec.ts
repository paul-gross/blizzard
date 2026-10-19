import { provideZonelessChangeDetection } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { HostPage } from './host-page';

const RUNNER = (runnerId: string, name: string, project: string) => ({
  runner_id: runnerId,
  name,
  state: 'serving',
  served: true,
  halted_reasons: [],
  pause: { local: false, hub: false, effective: false, local_reason: null },
  bindings: [{ project, workspace_id: 'ws', agents: 4 }],
  agents_in_use: 0,
  max_agents: 4,
});

const RUNNERS = {
  items: [
    RUNNER('rn_1', 'r1', 'console'),
    RUNNER('rn_2', 'r2', 'ops'),
    { runner_id: null, name: 'r-halted', state: 'halted', served: false, halted_reasons: ['no hub token'], bindings: [] },
  ],
};

const DASHBOARD = (runnerId: string, slug: string, name: string) => ({
  runner: {
    runner_id: runnerId,
    workspace_id: 'ws',
    pause: { local: false, hub: false, effective: false, local_reason: null },
    capacities: { used: 0, bindings: [] },
    hub: { endpoint: 'http://hub', reachable: true, last_contact_at: null, buffer_depth: 0 },
    last_tick_at: null,
    served_projects: [{ entry: slug, project_id: `proj_${slug}`, slug, name, color: 'amber', resolved: true, answered: true }],
  },
  asks: { items: [] },
  escalations: { items: [] },
  takeovers: { items: [] },
  facts: { items: [] },
  harness_health: { items: [] },
  fleet_summary: null,
});

const ENVIRONMENTS = {
  workspaces: [
    {
      workspace_id: 'ws',
      items: [
        { environment_id: 'e1', held: true, chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9', runner_id: 'rn_1', project: 'console' },
        { environment_id: 'e2', held: false },
      ],
    },
  ],
};

const REGISTRATION = {
  state: 'registered',
  host_id: 'hs_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  host_name: 'xps',
  answered_at: '2026-07-16T11:59:26.000Z',
  held_runner_ids: ['rn_1', 'rn_2'],
};

function route(overrides: Record<string, unknown> = {}) {
  return (method: string, path: string): unknown => {
    if (method !== 'GET') return {};
    if (path in overrides) return overrides[path];
    switch (path) {
      case '/api/host/runners':
        return RUNNERS;
      case '/api/host/workspaces':
        return { items: [{ workspace_id: 'ws', max_environments: 2 }] };
      case '/api/host/environments':
        return ENVIRONMENTS;
      case '/api/host/subscriptions':
        return { items: [] };
      case '/api/host/registration':
        return REGISTRATION;
      case '/runners/rn_1/api/dashboard':
        return DASHBOARD('rn_1', 'console', 'Console');
      case '/runners/rn_2/api/dashboard':
        return DASHBOARD('rn_2', 'ops', 'Ops');
      case '/runners/rn_1/api/leases':
      case '/runners/rn_2/api/leases':
        return { items: [] };
      default:
        return {};
    }
  };
}

describe('HostPage', () => {
  let stub: RequestClientStub | undefined;

  afterEach(() => stub?.restore());

  async function render(url = '/', handler = route()): Promise<{ fixture: ComponentFixture<HostPage>; el: HTMLElement }> {
    stub = stubRequestClient(runnerClient, handler);
    await TestBed.configureTestingModule({
      imports: [HostPage],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    await TestBed.inject(Router).navigateByUrl(url);
    const fixture = TestBed.createComponent(HostPage);
    await settle(fixture);
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  const names = (el: HTMLElement) =>
    [...el.querySelectorAll('[data-testid="host-runner-name"]')].map((node) => node.textContent?.trim());

  it('renders one runner row per runner the host serves or holds halted', async () => {
    const { el } = await render();

    expect(names(el)).toEqual(['r1', 'r2', 'r-halted']);
  });

  it('names the host and its registration in the title bar', async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="host-name"]')?.textContent?.trim()).toBe('xps');
    expect(el.querySelector('[data-testid="host-registration-state"]')?.textContent?.trim()).toBe('registered');
    expect(el.querySelector('[data-testid="host-registration-answered"]')).not.toBeNull();
  });

  it('narrows the rows to the runner the Runner filter names', async () => {
    const { el } = await render('/?runner=r2');

    expect(names(el)).toEqual(['r2']);
  });

  it('narrows the rows to the runners serving the Project filter’s project, dropping the halted one', async () => {
    const { el } = await render('/?project=console');

    expect(names(el)).toEqual(['r1']);
  });

  it('says no runner matches when the filters leave none', async () => {
    const { el } = await render('/?runner=r2&project=console');

    expect(names(el)).toEqual([]);
    expect(el.querySelector('[data-testid="host-runners-empty"]')).not.toBeNull();
  });

  it('lists the environments by workspace, each held one naming its project and runner', async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="host-workspace"]')?.textContent).toContain('1 / 2 in use');
    const held = el.querySelector<HTMLElement>('[data-testid="host-env-held"]')!;
    expect(held.querySelector('.project')?.textContent?.trim()).toBe('Console');
    expect(held.querySelector('.holder')?.textContent?.trim()).toBe('r1');
    expect(el.querySelectorAll('[data-testid="host-env-free"]')).toHaveLength(1);
  });

  it('says no workspaces are declared while none is reported', async () => {
    const { el } = await render('/', route({ '/api/host/workspaces': { items: [] }, '/api/host/environments': { workspaces: [] } }));

    expect(el.querySelector('[data-testid="host-environments-empty"]')).not.toBeNull();
  });

  it('shows the error state when the runner list fails', async () => {
    const { el } = await render('/', (method, path) => (path === '/api/host/runners' ? stubError(503, { detail: 'down' }) : route()(method, path)));

    expect(el.querySelector('[data-testid="host-runners"]')?.textContent).toContain('RUNNERS UNAVAILABLE');
  });

  it('shows the loading state before the runner list lands', async () => {
    const { el } = await render('/', (method, path) => (path === '/api/host/runners' ? new Promise(() => undefined) : route()(method, path)));

    expect(el.querySelector('[data-testid="host-runners"]')?.textContent).toContain('LOADING…');
  });
});

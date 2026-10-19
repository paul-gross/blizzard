import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { HostPanel } from './host-panel';

/** Two runners as the host reports them: one binding two workspaces, one held by its own brake. */
const RUNNERS: runnerApi.HostRunnerView[] = [
  {
    runner_id: 'rn_1',
    name: 'r-claude',
    state: 'serving',
    served: true,
    halted_reasons: [],
    pause: { local: false, hub: false, effective: false, local_reason: null },
    bindings: [
      { project: 'console', workspace_id: 'workspace-web', agents: 4 },
      { project: 'ops', workspace_id: 'workspace-local', agents: 2 },
    ],
    agents_in_use: 3,
    max_agents: 6,
  },
  {
    runner_id: 'rn_2',
    name: 'r-opencode',
    state: 'serving',
    served: true,
    halted_reasons: [],
    pause: { local: true, hub: false, effective: true, local_reason: 'OpenCode is not installed' },
    bindings: [{ project: 'winter', workspace_id: 'workspace-local', agents: 2 }],
    agents_in_use: 0,
    max_agents: 2,
  },
];

const DASHBOARD_RN_1 = {
  runner: {
    runner_id: 'rn_1',
    workspace_id: 'workspace-local',
    pause: { local: false, hub: false, effective: false, local_reason: null },
    capacities: {
      used: 3,
      bindings: [
        { project: 'console', project_id: 'proj_console', workspace_id: 'workspace-web', agents: 4, used: 2, free: 2 },
        { project: 'ops', project_id: 'proj_ops', workspace_id: 'workspace-local', agents: 2, used: 1, free: 1 },
      ],
    },
    hub: { endpoint: 'http://127.0.0.1:4522', reachable: true, last_contact_at: null, buffer_depth: 0 },
    last_tick_at: null,
  },
};

/** `rn_2`'s dashboard: its brake holds it, so its slot accounting names no binding in use. */
const DASHBOARD_RN_2 = {
  runner: { ...DASHBOARD_RN_1.runner, runner_id: 'rn_2', capacities: { used: 0, bindings: [] } },
};

const ENVIRONMENTS = {
  workspaces: [
    {
      workspace_id: 'workspace-local',
      items: [
        { environment_id: 'e1', chunk_id: 'ch_1', held_since: '2026-10-19T19:07:23.047Z', held: true, runner_id: 'rn_1', project: 'ops' },
        { environment_id: 'e2', chunk_id: null, held_since: null, held: false, runner_id: null, project: null },
      ],
    },
  ],
};

let stub: RequestClientStub;

afterEach(() => stub.restore());

async function render(
  options: { runners?: () => unknown; url?: string } = {},
): Promise<{ fixture: ComponentFixture<HostPanel>; el: HTMLElement }> {
  stub = stubRequestClient(runnerClient, (method, path) => {
    if (method !== 'GET') return {};
    switch (path) {
      case '/api/host/runners':
        return options.runners ? options.runners() : { items: RUNNERS };
      case '/api/host/subscriptions':
        return { items: [] };
      case '/api/host/workspaces':
        return { items: [{ workspace_id: 'workspace-local', max_environments: 2 }] };
      case '/api/host/environments':
        return ENVIRONMENTS;
      case '/runners/rn_1/api/dashboard':
        return DASHBOARD_RN_1;
      case '/runners/rn_2/api/dashboard':
        return DASHBOARD_RN_2;
      default:
        return {};
    }
  });
  await TestBed.configureTestingModule({
    imports: [HostPanel],
    providers: [
      provideZonelessChangeDetection(),
      provideRouter([{ path: '**', children: [] }]),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  if (options.url) await TestBed.inject(Router).navigateByUrl(options.url);
  const fixture = TestBed.createComponent(HostPanel);
  await settle(fixture);
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

const runnerNames = (el: HTMLElement): (string | undefined)[] =>
  Array.from(el.querySelectorAll('[data-testid="host-panel-runner-name"]')).map((name) => name.textContent?.replace(/\s+/g, ' ').trim());

/** Each binding line's accessible reading — its name, then its agents in use of those it declares. */
const bindingLines = (runner: Element): (string | null)[] =>
  Array.from(runner.querySelectorAll('[data-testid="host-panel-runner-binding"] [data-testid="slot-bar-gauge"]')).map((gauge) =>
    gauge.getAttribute('aria-label'),
  );

describe('HostPanel', () => {
  it('lists every runner on the host under All, each with its brake', async () => {
    const { el } = await render();

    expect(runnerNames(el)).toEqual(['r-claude', 'r-opencode']);
    const [claude, opencode] = Array.from(el.querySelectorAll('[data-testid="host-panel-runner"]'));
    expect(claude.classList).not.toContain('paused');
    expect(opencode.classList).toContain('paused');
    expect(opencode.querySelector('[data-testid="host-panel-runner-local-paused"]')).not.toBeNull();
  });

  it("fills each binding's agents in use from the runner's own dashboard, the workspace beside a runner binding two", async () => {
    const { el } = await render();
    const [claude, opencode] = Array.from(el.querySelectorAll('[data-testid="host-panel-runner"]'));

    expect(bindingLines(claude)).toEqual(['console · workspace-web 2/4 agents', 'ops · workspace-local 1/2 agents']);
    // `rn_2`'s slot accounting names none of its bindings, so its one binding reads 0 in use.
    expect(bindingLines(opencode)).toEqual(['winter 0/2 agents']);
  });

  it('lists only the runner the Runner filter names', async () => {
    const { el } = await render({ url: '/board?runner=r-opencode' });

    expect(runnerNames(el)).toEqual(['r-opencode']);
  });

  it('says so when no runner matches the filter', async () => {
    const { el } = await render({ url: '/board?runner=gone' });

    expect(runnerNames(el)).toEqual([]);
    expect(el.querySelector('[data-testid="host-panel-runners"]')?.textContent).toContain('no runner matches the filter');
  });

  it("keeps the host's environments whole, one pool per workspace", async () => {
    const { el } = await render({ url: '/board?runner=r-opencode' });

    const pools = Array.from(el.querySelectorAll('[data-testid="host-panel-pool"]'));
    expect(pools.map((pool) => pool.getAttribute('data-workspace-id'))).toEqual(['workspace-local']);
  });

  it('says none declared when the host declares no subscription', async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="host-panel-subscriptions"]')?.textContent).toContain('none declared');
  });

  it("reads HOST UNAVAILABLE once the host's runner list fails", async () => {
    const { el } = await render({ runners: () => stubError(502, { detail: 'down' }) });

    expect(el.textContent).toContain('HOST UNAVAILABLE');
    expect(el.querySelector('[data-testid="host-panel-runner"]')).toBeNull();
  });
});

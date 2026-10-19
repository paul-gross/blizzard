import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { injectPanelAsks, injectPanelDashboards, injectPanelLeases } from './panel-reads';

/** The panel's fan-out reads as the board mounts them: the Runner filter scopes each. */
@Component({
  selector: 'app-test-panel-reads-host',
  template: '',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class PanelReadsHost {
  readonly dashboards = injectPanelDashboards();
  readonly leases = injectPanelLeases();
  readonly asks = injectPanelAsks();
}

function hostRunner(runnerId: string, name: string, overrides: Partial<runnerApi.HostRunnerView> = {}): runnerApi.HostRunnerView {
  return {
    runner_id: runnerId,
    name,
    state: 'serving',
    served: true,
    halted_reasons: [],
    pause: { local: false, hub: false, effective: false, local_reason: null },
    bindings: [{ project: `proj-of-${name}`, workspace_id: 'ws', agents: 4 }],
    agents_in_use: 0,
    max_agents: 4,
    ...overrides,
  };
}

function dashboard(runnerId: string): unknown {
  return {
    runner: { runner_id: runnerId },
    asks: { items: [{ question_id: `q-of-${runnerId}`, chunk_id: 'ch_1', lease_id: null, session_id: null, question: '?', asked_at: '2026-07-16T11:00:00+00:00' }] },
    escalations: { items: [] },
    facts: { items: [] },
  };
}

/** A host serving `rn_1` (`r1`) and `rn_2` (`r2`), holding `rn_3` halted; `failing` names a
 * runner whose every read answers 503. */
function routeFor(failing: string | null) {
  return (method: string, path: string): unknown => {
    if (method !== 'GET') return {};
    if (path === '/api/host/runners') {
      return { items: [hostRunner('rn_1', 'r1'), hostRunner('rn_2', 'r2'), hostRunner('rn_3', 'r3', { state: 'halted', served: false })] };
    }
    if (path === '/api/host/environments') return { workspaces: [] };
    const match = /^\/runners\/([^/]+)\/api\/(.+)$/.exec(path);
    if (match === null) return {};
    const [, runnerId, rest] = match;
    if (runnerId === failing) return stubError(503, { detail: 'runner unreachable' });
    if (rest === 'dashboard') return dashboard(runnerId);
    if (rest === 'leases') return { items: [{ lease_id: `lease-of-${runnerId}`, chunk_id: 'ch_1', state: 'running', created_at: '2026-07-16T11:00:00+00:00' }] };
    return {};
  };
}

describe('the panel’s fan-out reads', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function mount(failing: string | null, url = '/') {
    stub = stubRequestClient(runnerClient, routeFor(failing));
    await TestBed.configureTestingModule({
      imports: [PanelReadsHost],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    await TestBed.inject(Router).navigateByUrl(url);
    const fixture = TestBed.createComponent(PanelReadsHost);
    await settle(fixture);
    return fixture.componentInstance;
  }

  it('read every serving runner while the Runner filter is All, never a halted one', async () => {
    const host = await mount(null);

    expect(host.dashboards.data().map(({ owner }) => owner.name)).toEqual(['r1', 'r2']);
    expect(stub.forRoute('/runners/rn_3/api/dashboard', 'GET')).toHaveLength(0);
    expect(stub.forRoute('/runners/rn_3/api/leases', 'GET')).toHaveLength(0);
  });

  it('read only the named runner’s items under ?runner=', async () => {
    const host = await mount(null, '/?runner=r2');

    expect(host.dashboards.data().map(({ owner }) => owner.runnerId)).toEqual(['rn_2']);
    expect(host.leases.data().map((lease) => lease.lease_id)).toEqual(['lease-of-rn_2']);
    expect(host.asks.data().map((ask) => ask.owner.runnerId)).toEqual(['rn_2']);
    expect(stub.forRoute('/runners/rn_1/api/leases', 'GET')).toHaveLength(0);
    // Every serving runner's dashboard is still read once: the project catalog spans the host.
    expect(stub.forRoute('/runners/rn_1/api/dashboard', 'GET')).toHaveLength(1);
  });

  it('narrow merged items to the project under ?project=, attributing each through its runner’s only project', async () => {
    const host = await mount(null, '/?project=proj-of-r1');

    expect(host.leases.data().map((lease) => [lease.lease_id, lease.projectSlug])).toEqual([['lease-of-rn_1', 'proj-of-r1']]);
    expect(host.asks.data().map((ask) => ask.question_id)).toEqual(['q-of-rn_1']);
  });

  it('keep the other runners’ items when one runner’s reads fail — no error until every runner’s does', async () => {
    const host = await mount('rn_2');

    expect(host.dashboards.isError()).toBe(false);
    expect(host.leases.isError()).toBe(false);
    expect(host.dashboards.data().map(({ owner }) => owner.runnerId)).toEqual(['rn_1']);
    expect(host.leases.data().map((lease) => lease.lease_id)).toEqual(['lease-of-rn_1']);
    expect(host.asks.data().map((ask) => ask.owner.runnerId)).toEqual(['rn_1']);
  });

  it('read as an error once the only covered runner’s read fails', async () => {
    const host = await mount('rn_2', '/?runner=r2');

    expect(host.dashboards.isError()).toBe(true);
    expect(host.leases.isError()).toBe(true);
    expect(host.dashboards.data()).toEqual([]);
  });
});

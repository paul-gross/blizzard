import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type runnerApi } from 'fleet';
import {
  type RequestClientStub,
  hostRunnerView,
  runnerAskView,
  runnerDashboardView,
  runnerFactView,
  runnerLeaseView,
  runnerStatusView,
  settle,
  stubError,
  stubRunnerClient,
} from 'fleet/testing';

import { injectPanelAsks, injectPanelDashboards, injectPanelFacts, injectPanelLeases } from './panel-reads';

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
  readonly facts = injectPanelFacts();
}

function hostRunner(runnerId: string, name: string, overrides: Partial<runnerApi.HostRunnerView> = {}): runnerApi.HostRunnerView {
  return hostRunnerView({
    runner_id: runnerId,
    name,
    bindings: [{ project: `proj-of-${name}`, workspace_id: 'ws', agents: 4 }],
    max_agents: 4,
    ...overrides,
  });
}

function projectOf(runnerId: string): string {
  return `proj-of-r${runnerId.slice('rn_'.length)}`;
}

function dashboard(runnerId: string): runnerApi.DashboardView {
  const project_id = projectOf(runnerId);
  return runnerDashboardView({
    runner: runnerStatusView({ runner_id: runnerId }),
    asks: {
      items: [runnerAskView({ question_id: `q-of-${runnerId}`, chunk_id: 'ch_1', project_id, asked_at: '2026-07-16T11:00:00+00:00' })],
    },
    // A fact on a chunk no lease in the window names — attributed by its own project all the same.
    facts: {
      items: [runnerFactView({ seq: 1, kind: 'note', chunk_id: 'ch_unleased', project_id, created_at: '2026-07-16T11:00:00+00:00' })],
    },
  });
}

/** A host serving `rn_1` (`r1`) and `rn_2` (`r2`), holding `rn_3` halted; `failing` names a
 * runner whose every read answers 503. */
function stubHost(failing: string | null): RequestClientStub {
  const unreachable = () => stubError(503, { detail: 'runner unreachable' });
  return stubRunnerClient({
    'GET /api/host/runners': () => ({
      items: [hostRunner('rn_1', 'r1'), hostRunner('rn_2', 'r2'), hostRunner('rn_3', 'r3', { state: 'halted', served: false })],
    }),
    'GET /api/host/environments': () => ({ workspaces: [] }),
    'GET /runners/{runner_id}/api/dashboard': (_request, { runner_id }) =>
      runner_id === failing ? unreachable() : dashboard(runner_id),
    'GET /runners/{runner_id}/api/leases': (_request, { runner_id }) =>
      runner_id === failing
        ? unreachable()
        : {
            items: [
              runnerLeaseView({
                lease_id: `lease-of-${runner_id}`,
                chunk_id: 'ch_1',
                project_id: projectOf(runner_id),
                state: 'running',
                created_at: '2026-07-16T11:00:00+00:00',
              }),
            ],
          },
  });
}

describe('the panel’s fan-out reads', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function mount(failing: string | null, url = '/') {
    stub = stubHost(failing);
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

  it('narrow merged items to the project under ?project=, attributing leases, asks and facts by their own wire project', async () => {
    const host = await mount(null, '/?project=proj-of-r1');

    expect(host.leases.data().map((lease) => [lease.lease_id, lease.projectSlug])).toEqual([['lease-of-rn_1', 'proj-of-r1']]);
    expect(host.asks.data().map((ask) => ask.question_id)).toEqual(['q-of-rn_1']);
    expect(host.facts.data().map((fact) => [fact.owner.runnerId, fact.chunk_id, fact.projectSlug])).toEqual([['rn_1', 'ch_unleased', 'proj-of-r1']]);
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

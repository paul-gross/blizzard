import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { injectChunkDetailQuery, runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { injectPanelDashboards, injectPanelLeases } from './panel-reads';
import { runnerChunkDetailKey, runnerDashboardKey, runnerLeasesKey } from './query-keys';

/** Every per-runner read the board mounts, fanned out over both runners, plus one chunk
 * detail per runner for a chunk id the two share. */
@Component({
  selector: 'app-test-runner-reads-host',
  template: '',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class RunnerReadsHost {
  readonly dashboards = injectPanelDashboards('all');
  readonly leases = injectPanelLeases({ scope: 'all', byProject: false });
  readonly detailOn1 = injectChunkDetailQuery(
    () => ({ plane: 'runner', client: runnerClient, runnerId: 'rn_1' }),
    () => 'ch_1',
  );
  readonly detailOn2 = injectChunkDetailQuery(
    () => ({ plane: 'runner', client: runnerClient, runnerId: 'rn_2' }),
    () => 'ch_1',
  );
}

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

/** Each runner answers with bodies naming itself, so a read served from the wrong
 * runner's cache entry shows. */
function routeFor(method: string, path: string): unknown {
  if (method !== 'GET') return {};
  if (path === '/api/host/runners') return { items: [hostRunner('rn_1', 'r1'), hostRunner('rn_2', 'r2')] };
  if (path === '/api/host/environments') return { workspaces: [] };
  const match = /^\/runners\/([^/]+)\/api\/(.+)$/.exec(path);
  if (match === null) return {};
  const [, runnerId, rest] = match;
  if (rest === 'dashboard') return { runner: { runner_id: runnerId } };
  if (rest === 'leases') {
    return { items: [{ lease_id: `lease-of-${runnerId}`, chunk_id: 'ch_1', state: 'running', created_at: '2026-07-16T11:00:00+00:00' }] };
  }
  if (rest === 'chunks/ch_1') return { chunk_id: 'ch_1', status: 'running', graph_name: runnerId };
  return {};
}

describe('two runners on one host', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function mount(queryClient: QueryClient) {
    stub = stubRequestClient(runnerClient, routeFor);
    await TestBed.configureTestingModule({
      imports: [RunnerReadsHost],
      providers: [provideZonelessChangeDetection(), provideRouter([]), provideTanStackQuery(queryClient)],
    }).compileComponents();
    const fixture = TestBed.createComponent(RunnerReadsHost);
    await settle(fixture);
    return fixture;
  }

  it('read each runner under its own key — never sharing a dashboard, lease, or chunk-detail cache entry', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const fixture = await mount(queryClient);
    const host = fixture.componentInstance;

    // Each merged item carries the runner it was read off, and its body is that runner's.
    expect(host.dashboards.data().map(({ owner, dashboard }) => [owner.runnerId, dashboard.runner.runner_id])).toEqual([
      ['rn_1', 'rn_1'],
      ['rn_2', 'rn_2'],
    ]);
    expect(host.leases.data().map((lease) => [lease.owner.runnerId, lease.lease_id])).toEqual(
      expect.arrayContaining([
        ['rn_1', 'lease-of-rn_1'],
        ['rn_2', 'lease-of-rn_2'],
      ]),
    );
    expect(host.detailOn1.data()?.graph_name).toBe('rn_1');
    expect(host.detailOn2.data()?.graph_name).toBe('rn_2');

    for (const runnerId of ['rn_1', 'rn_2']) {
      expect(stub.forRoute(`/runners/${runnerId}/api/dashboard`, 'GET')).toHaveLength(1);
      expect(stub.forRoute(`/runners/${runnerId}/api/leases`, 'GET')).toHaveLength(1);
      expect(stub.forRoute(`/runners/${runnerId}/api/chunks/ch_1`, 'GET')).toHaveLength(1);
    }
    const cached = (key: readonly unknown[]) => queryClient.getQueryData<unknown>(key);
    expect(cached(runnerDashboardKey('rn_1'))).toMatchObject({ runner: { runner_id: 'rn_1' } });
    expect(cached(runnerDashboardKey('rn_2'))).toMatchObject({ runner: { runner_id: 'rn_2' } });
    expect(cached(runnerLeasesKey('rn_1'))).toEqual([expect.objectContaining({ lease_id: 'lease-of-rn_1' })]);
    expect(cached(runnerLeasesKey('rn_2'))).toEqual([expect.objectContaining({ lease_id: 'lease-of-rn_2' })]);
    expect(cached(runnerChunkDetailKey('rn_1', 'ch_1'))).toMatchObject({ graph_name: 'rn_1' });
    expect(cached(runnerChunkDetailKey('rn_2', 'ch_1'))).toMatchObject({ graph_name: 'rn_2' });
  });

  it('stale only the named runner’s reads when one runner’s keys are invalidated, as its live frame does', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const fixture = await mount(queryClient);

    await queryClient.invalidateQueries({ queryKey: runnerLeasesKey('rn_1') });
    await queryClient.invalidateQueries({ queryKey: runnerDashboardKey('rn_1') });
    await queryClient.invalidateQueries({ queryKey: runnerChunkDetailKey('rn_1', 'ch_1') });
    await settle(fixture);

    expect(stub.forRoute('/runners/rn_1/api/leases', 'GET')).toHaveLength(2);
    expect(stub.forRoute('/runners/rn_1/api/dashboard', 'GET')).toHaveLength(2);
    expect(stub.forRoute('/runners/rn_1/api/chunks/ch_1', 'GET')).toHaveLength(2);
    expect(stub.forRoute('/runners/rn_2/api/leases', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/runners/rn_2/api/dashboard', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/runners/rn_2/api/chunks/ch_1', 'GET')).toHaveLength(1);
  });
});

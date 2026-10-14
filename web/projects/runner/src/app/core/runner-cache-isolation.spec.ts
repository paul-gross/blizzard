import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { injectChunkDetailQuery, runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { ACTIVE_RUNNER, injectActiveRunnerTarget } from './active-runner';
import { injectRunnerLeasesQuery } from './leases.query';
import { runnerChunkDetailKey, runnerDashboardKey, runnerLeasesKey } from './query-keys';
import { injectRunnerDashboardQuery } from './status.query';

/** Every per-runner read the board mounts, under whichever runner is active. */
@Component({
  selector: 'app-test-runner-reads-host',
  template: '',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class RunnerReadsHost {
  readonly dashboard = injectRunnerDashboardQuery();
  readonly leases = injectRunnerLeasesQuery();
  private readonly target = injectActiveRunnerTarget();
  readonly chunkDetail = injectChunkDetailQuery(
    () => this.target(),
    () => 'ch_1',
  );
}

/** Each runner answers with bodies naming itself, so a read served from the wrong
 * runner's cache entry shows. */
function routeFor(method: string, path: string): unknown {
  const match = /^\/runners\/([^/]+)\/api\/(.+)$/.exec(path);
  if (method !== 'GET' || match === null) return {};
  const [, runnerId, rest] = match;
  if (rest === 'dashboard') return { runner: { runner_id: runnerId } };
  if (rest === 'leases') return { items: [{ lease_id: `lease-of-${runnerId}`, chunk_id: 'ch_1' }] };
  if (rest === 'chunks/ch_1') return { chunk_id: 'ch_1', status: 'running', graph_name: runnerId };
  return {};
}

describe('two runners on one host', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  it('never share a board, lease, or chunk-detail cache entry', async () => {
    stub = stubRequestClient(runnerClient, routeFor);
    const active = signal<string | null>('rn_1');
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await TestBed.configureTestingModule({
      imports: [RunnerReadsHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(queryClient),
        { provide: ACTIVE_RUNNER, useValue: active },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(RunnerReadsHost);
    await settle(fixture);
    const host = fixture.componentInstance;

    expect(host.dashboard.data()?.runner.runner_id).toBe('rn_1');
    expect(host.leases.data()?.[0].lease_id).toBe('lease-of-rn_1');
    expect(host.chunkDetail.data()?.graph_name).toBe('rn_1');

    active.set('rn_2');
    await settle(fixture);

    expect(host.dashboard.data()?.runner.runner_id).toBe('rn_2');
    expect(host.leases.data()?.[0].lease_id).toBe('lease-of-rn_2');
    expect(host.chunkDetail.data()?.graph_name).toBe('rn_2');

    for (const runnerId of ['rn_1', 'rn_2']) {
      expect(stub.forRoute(`/runners/${runnerId}/api/dashboard`, 'GET')).toHaveLength(1);
      expect(stub.forRoute(`/runners/${runnerId}/api/leases`, 'GET')).toHaveLength(1);
      expect(stub.forRoute(`/runners/${runnerId}/api/chunks/ch_1`, 'GET')).toHaveLength(1);
    }
    const cached = (key: readonly unknown[]) => queryClient.getQueryData<unknown>(key);
    expect(cached(runnerDashboardKey('rn_1'))).not.toBe(cached(runnerDashboardKey('rn_2')));
    expect(cached(runnerLeasesKey('rn_1'))).not.toBe(cached(runnerLeasesKey('rn_2')));
    expect(cached(runnerChunkDetailKey('rn_1', 'ch_1'))).not.toBe(cached(runnerChunkDetailKey('rn_2', 'ch_1')));
    expect(cached(runnerChunkDetailKey('rn_1', 'ch_1'))).toMatchObject({ graph_name: 'rn_1' });
  });

  it('hold every runner read on a host page, where no runner is active', async () => {
    stub = stubRequestClient(runnerClient, routeFor);
    await TestBed.configureTestingModule({
      imports: [RunnerReadsHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient()),
        { provide: ACTIVE_RUNNER, useValue: signal<string | null>(null) },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(RunnerReadsHost);
    await settle(fixture);

    expect(stub.requests).toEqual([]);
  });
});

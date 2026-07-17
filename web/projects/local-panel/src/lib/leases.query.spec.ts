import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { injectRunnerLeasesQuery } from './leases.query';
import { runnerLeasesKey } from './query-keys';
import { settle } from './testing/settle';
import { RouteError, type RunnerClientStub, stubRunnerClient } from './testing/stub-runner-client';

const LEASES = {
  items: [
    {
      lease_id: 'L-903',
      chunk_id: 'C-125',
      graph_id: 'gr_1',
      node_id: 'nd_build',
      node_name: 'build',
      epoch: 2,
      session_id: 'sess-77',
      pid: 4821,
      environment_id: 'beta',
      workdir: '/ws/beta',
      created_at: '2026-07-16T11:00:00+00:00',
      last_heartbeat_at: '2026-07-16T11:59:26+00:00',
      state: 'running',
    },
  ],
};

/** A minimal host so the query — a `Component` field initializer concern — runs
 * inside a real injection context, mirroring how every consumer actually calls it. */
@Component({
  selector: 'fleet-test-leases-query-host',
  template: '',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class LeasesQueryHost {
  readonly query = injectRunnerLeasesQuery();
}

describe('injectRunnerLeasesQuery', () => {
  let stub: RunnerClientStub | undefined;

  afterEach(() => stub?.restore());

  it('is namespaced under runner (fleet/local split)', () => {
    expect(runnerLeasesKey).toEqual(['runner', 'leases']);
  });

  it('reads GET /api/leases through the generated runner client and returns its items', async () => {
    stub = stubRunnerClient((method, path) => (method === 'GET' && path === '/api/leases' ? LEASES : {}));
    await TestBed.configureTestingModule({
      imports: [LeasesQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(LeasesQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toEqual(LEASES.items);
    expect(stub.forRoute('/api/leases', 'GET')).toHaveLength(1);
  });

  it('returns an empty array when the runner genuinely holds no active leases', async () => {
    stub = stubRunnerClient((method, path) => (method === 'GET' && path === '/api/leases' ? { items: [] } : {}));
    await TestBed.configureTestingModule({
      imports: [LeasesQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(LeasesQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.isSuccess()).toBe(true);
    expect(fixture.componentInstance.query.data()).toEqual([]);
  });

  it('re-reads GET /api/leases every 5s — the poll is the runner panel\'s only liveness signal', async () => {
    // Unlike `fleet`'s hub queries, where the poll is a floor under an SSE stream,
    // the runner has no event stream: drop the interval and the panel silently
    // becomes a permanently static snapshot. This pins the refresh as behavior
    // (a second request actually fires), not as a constant equal to 5000.
    vi.useFakeTimers();
    try {
      stub = stubRunnerClient((method, path) => (method === 'GET' && path === '/api/leases' ? LEASES : {}));
      await TestBed.configureTestingModule({
        imports: [LeasesQueryHost],
        providers: [
          provideZonelessChangeDetection(),
          provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        ],
      }).compileComponents();
      const fixture = TestBed.createComponent(LeasesQueryHost);
      fixture.detectChanges();
      await vi.advanceTimersByTimeAsync(0);

      expect(stub.forRoute('/api/leases', 'GET')).toHaveLength(1);

      // Just shy of the interval: still the one initial read.
      await vi.advanceTimersByTimeAsync(4_000);
      expect(stub.forRoute('/api/leases', 'GET')).toHaveLength(1);

      // Crossing 5s fires the poll, and it keeps firing.
      await vi.advanceTimersByTimeAsync(1_000);
      expect(stub.forRoute('/api/leases', 'GET')).toHaveLength(2);

      await vi.advanceTimersByTimeAsync(5_000);
      expect(stub.forRoute('/api/leases', 'GET')).toHaveLength(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it('surfaces a 503 (store unwired) as an error — never a silent empty list', async () => {
    stub = stubRunnerClient((method, path) => {
      if (method === 'GET' && path === '/api/leases') throw new RouteError(503, 'lease store not wired');
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [LeasesQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(LeasesQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(true);
    expect(fixture.componentInstance.query.data()).toBeUndefined();
  });
});

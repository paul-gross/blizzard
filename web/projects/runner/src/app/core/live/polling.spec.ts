import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, injectQuery, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS, injectChunkDetailQuery, runnerClient } from 'fleet';
import { type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { injectRunnerSessionQuery } from '../identity/auth.query';
import { runnerLeasesQueryOptions } from '../leases.query';
import { injectRunnerDashboardQuery } from '../status.query';

/** A minimal host mounting all four backstop-governed reads in one injection context —
 * `chunkId` is fixed `null` (unselected), the gating {@link injectChunkDetailQuery}
 * itself branches on. */
@Component({
  selector: 'app-test-polling-host',
  template: '',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class PollingHost {
  readonly dashboard = injectRunnerDashboardQuery(() => 'rn_1');
  readonly leases = injectQuery(() => runnerLeasesQueryOptions('rn_1'));
  readonly session = injectRunnerSessionQuery();
  readonly chunkDetail = injectChunkDetailQuery(
    () => ({ plane: 'runner', client: runnerClient, runnerId: 'rn_1' }),
    () => null,
  );
}

/**
 * The poll backstop's floors, proven together: over an idle multi-minute
 * window with no SSE events at all, only the two reads the backstop leaves on a timer
 * (dashboard, leases) re-fire — session carries no `refetchInterval` at all,
 * and chunk-detail's backstop is gated on a selection this host never
 * makes, so it never even issues its one initial read.
 */
describe('the panel\'s poll floors, idle and unselected', () => {
  let stub: RequestClientStub | undefined;

  afterEach(() => {
    stub?.restore();
    vi.useRealTimers();
  });

  it('re-fires only dashboard and leases across two backstop intervals; session polls once, chunk-detail never', async () => {
    vi.useFakeTimers();
    stub = stubRequestClient(runnerClient, (method, path) => {
      if (method === 'GET' && path === '/runners/rn_1/api/dashboard') return {};
      if (method === 'GET' && path === '/runners/rn_1/api/leases') return { items: [] };
      if (method === 'GET' && path === '/api/auth/session') return { auth_enabled: true, username: 'alice' };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [PollingHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(PollingHost);
    fixture.detectChanges();
    await vi.advanceTimersByTimeAsync(0);

    // The one initial read each enabled query issues on mount.
    expect(stub.forRoute('/runners/rn_1/api/dashboard', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/runners/rn_1/api/leases', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/api/auth/session', 'GET')).toHaveLength(1);
    expect(stub.requests.filter((r) => /^\/runners\/rn_1\/api\/chunks\//.test(r.path))).toHaveLength(0);

    // Idle for a window spanning two backstop intervals — no SSE event ever fires.
    await vi.advanceTimersByTimeAsync(RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS * 2 + 1_000);

    // Dashboard and leases: the backstop kept firing.
    expect(stub.forRoute('/runners/rn_1/api/dashboard', 'GET').length).toBeGreaterThanOrEqual(3);
    expect(stub.forRoute('/runners/rn_1/api/leases', 'GET').length).toBeGreaterThanOrEqual(3);
    // Session: no refetch interval — just the one mount-time read.
    expect(stub.forRoute('/api/auth/session', 'GET')).toHaveLength(1);
    // Chunk-detail: disabled the whole time — never issued even its first read.
    expect(stub.requests.filter((r) => /^\/runners\/rn_1\/api\/chunks\//.test(r.path))).toHaveLength(0);
    expect(fixture.componentInstance.chunkDetail.isPending()).toBe(true);
  });
});

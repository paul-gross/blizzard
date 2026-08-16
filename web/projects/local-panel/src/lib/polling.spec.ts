import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { injectRunnerSessionQuery } from './auth.query';
import { injectChunkDetailQuery } from './chunk-detail.query';
import { injectRunnerLeasesQuery } from './leases.query';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS } from './polling';
import { injectRunnerDashboardQuery } from './status.query';

/** A minimal host mounting all four D7-governed reads in one injection context —
 * `chunkId` is fixed `null` (unselected), the gating {@link injectChunkDetailQuery}
 * itself branches on. */
@Component({
  selector: 'local-test-polling-host',
  template: '',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class PollingHost {
  readonly dashboard = injectRunnerDashboardQuery();
  readonly leases = injectRunnerLeasesQuery();
  readonly session = injectRunnerSessionQuery();
  readonly chunkDetail = injectChunkDetailQuery(() => null);
}

/**
 * D7's floors, proven together (blizzard#317 Phase 4): over an idle multi-minute
 * window with no SSE events at all, only the two reads D7 leaves on a timer
 * (dashboard, leases) re-fire — session carries no `refetchInterval` at all
 * (removed), and chunk-detail's backstop is gated on a selection this host never
 * makes, so it never even issues its one initial read.
 */
describe('the panel\'s D7 poll floors, idle and unselected', () => {
  let stub: RequestClientStub | undefined;

  afterEach(() => {
    stub?.restore();
    vi.useRealTimers();
  });

  it('re-fires only dashboard and leases across two backstop intervals; session polls once, chunk-detail never', async () => {
    vi.useFakeTimers();
    stub = stubRequestClient(runnerClient, (method, path) => {
      if (method === 'GET' && path === '/api/dashboard') return {};
      if (method === 'GET' && path === '/api/leases') return { items: [] };
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
    expect(stub.forRoute('/api/dashboard', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/api/leases', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/api/auth/session', 'GET')).toHaveLength(1);
    expect(stub.requests.filter((r) => /^\/api\/chunks\//.test(r.path))).toHaveLength(0);

    // Idle for a window spanning two backstop intervals — no SSE event ever fires.
    await vi.advanceTimersByTimeAsync(RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS * 2 + 1_000);

    // Dashboard and leases: the backstop kept firing.
    expect(stub.forRoute('/api/dashboard', 'GET').length).toBeGreaterThanOrEqual(3);
    expect(stub.forRoute('/api/leases', 'GET').length).toBeGreaterThanOrEqual(3);
    // Session: removed (D7) — still just the one mount-time read.
    expect(stub.forRoute('/api/auth/session', 'GET')).toHaveLength(1);
    // Chunk-detail: disabled the whole time — never issued even its first read.
    expect(stub.requests.filter((r) => /^\/api\/chunks\//.test(r.path))).toHaveLength(0);
    expect(fixture.componentInstance.chunkDetail.isPending()).toBe(true);
  });
});

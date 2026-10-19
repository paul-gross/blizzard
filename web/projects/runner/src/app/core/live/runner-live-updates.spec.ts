import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  backoffDelay,
  EVENT_SOURCE_FACTORY,
  type EventSourceFactory,
  type FleetEventSource,
  INVALIDATION_COALESCE_WINDOW_MS,
} from 'fleet';
import { vi } from 'vitest';

import { hostKey, runnerChunkDetailKey, runnerDashboardKey, runnerLeasesKey } from '../query-keys';
import { RunnerLiveUpdates, STREAM_REARM_MAX_ATTEMPTS } from './runner-live-updates';
import { SessionRecovery } from '../identity/session-recovery';

/** EventSource stand-in with named-listener and auth-failure support — jsdom ships
 * none, and reconnect plus the stream's `401` channel must be driven deterministically.
 * Mirrors `fleet`'s own `sse.service.spec.ts`/`fleet-live.spec.ts` fakes. */
class FakeEventSource {
  static readonly instances: FakeEventSource[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onautherror: (() => void) | null = null;
  private readonly listeners = new Map<string, (event: MessageEvent) => void>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, handler: (event: MessageEvent) => void): void {
    this.listeners.set(type, handler);
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  emitNamed(type: string, data: string, lastEventId = ''): void {
    this.listeners.get(type)?.({ data, lastEventId } as MessageEvent);
  }

  hardError(): void {
    this.readyState = 2;
    this.onerror?.();
  }

  authError(): void {
    this.readyState = 2;
    this.onautherror?.();
  }

  close(): void {
    this.readyState = 2;
  }
}

describe('RunnerLiveUpdates', () => {
  let queryClient: QueryClient;
  let recoverSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.instances.length = 0;
    queryClient = new QueryClient();
    const factory: EventSourceFactory = (url) => new FakeEventSource(url) as unknown as FleetEventSource;
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(queryClient),
        { provide: EVENT_SOURCE_FACTORY, useValue: factory },
      ],
    });
    const recovery = TestBed.inject(SessionRecovery);
    recoverSpy = vi.spyOn(recovery, 'recoverFromUnauthenticated').mockResolvedValue('not-applicable');
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** The query keys every `invalidateQueries` call named, after one coalesce window. */
  function emitAndCollect(frames: readonly [string, object][]): unknown[] {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());
    const source = FakeEventSource.instances[0];
    source.open();
    frames.forEach(([kind, payload], i) => source.emitNamed(kind, JSON.stringify(payload), String(i + 1)));
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);
    return invalidate.mock.calls.map((call) => call[0]?.queryKey);
  }

  it('invalidates the named runner’s leases, dashboard, and chunk, and the host, on lease-changed', () => {
    const keys = emitAndCollect([
      ['lease-changed', { runner_id: 'rn_1', lease_id: 'lease_1', chunk_id: 'ch_1', cause: 'transitioned' }],
    ]);

    expect(keys).toContainEqual(runnerLeasesKey('rn_1'));
    expect(keys).toContainEqual(runnerDashboardKey('rn_1'));
    expect(keys).toContainEqual(runnerChunkDetailKey('rn_1', 'ch_1'));
    expect(keys).toContainEqual(hostKey);
  });

  it('invalidates only the named runner’s dashboard and chunk on ask-changed — not its leases list', () => {
    const keys = emitAndCollect([
      ['ask-changed', { runner_id: 'rn_1', lease_id: 'lease_1', chunk_id: 'ch_2', question_id: 'q_1', cause: 'asked' }],
    ]);

    expect(keys).toContainEqual(runnerDashboardKey('rn_1'));
    expect(keys).toContainEqual(runnerChunkDetailKey('rn_1', 'ch_2'));
    expect(keys).not.toContainEqual(runnerLeasesKey('rn_1'));
  });

  it('invalidates the named runner’s dashboard and chunk on escalation-changed and takeover-changed', () => {
    const keys = emitAndCollect([
      ['escalation-changed', { runner_id: 'rn_1', chunk_id: 'ch_3', cause: 'opened' }],
      ['takeover-changed', { runner_id: 'rn_1', chunk_id: 'ch_4', takeover_id: 'tko_1', cause: 'opened' }],
    ]);

    expect(keys).toContainEqual(runnerDashboardKey('rn_1'));
    expect(keys).toContainEqual(runnerChunkDetailKey('rn_1', 'ch_3'));
    expect(keys).toContainEqual(runnerChunkDetailKey('rn_1', 'ch_4'));
  });

  it('invalidates only the host on environment-changed, which names no runner', () => {
    const keys = emitAndCollect([['environment-changed', { chunk_id: 'ch_5', environment_id: 'env_1', cause: 'bound' }]]);

    expect(keys).toEqual([hostKey]);
  });

  it('invalidates the named runner’s dashboard on a runner-wide fact-changed (chunk_id null) with no chunk key', () => {
    const keys = emitAndCollect([
      ['fact-changed', { runner_id: 'rn_1', seq: 1, kind: 'event.recorded', chunk_id: null, lease_id: null }],
    ]);

    expect(keys).toContainEqual(runnerDashboardKey('rn_1'));
    expect(keys.every((key) => (key as readonly unknown[])[1] !== 'chunk')).toBe(true);
  });

  it('invalidates the named runner’s dashboard and chunk on a chunk-scoped fact-changed', () => {
    const keys = emitAndCollect([
      ['fact-changed', { runner_id: 'rn_1', seq: 2, kind: 'chunk-facts-appended', chunk_id: 'ch_6', lease_id: 'lease_2' }],
    ]);

    expect(keys).toContainEqual(runnerDashboardKey('rn_1'));
    expect(keys).toContainEqual(runnerChunkDetailKey('rn_1', 'ch_6'));
  });

  it('invalidates only the framing runner’s keys plus the host — never another runner’s', () => {
    const keys = emitAndCollect([
      ['lease-changed', { runner_id: 'rn_1', lease_id: 'lease_1', chunk_id: 'ch_1', cause: 'spawned' }],
      ['fact-changed', { runner_id: 'rn_1', seq: 4, kind: 'chunk-facts-appended', chunk_id: 'ch_1', lease_id: 'lease_1' }],
    ]);

    expect(keys.length).toBeGreaterThan(0);
    for (const key of keys) {
      const root = (key as readonly unknown[])[0];
      expect(root === 'rn_1' || root === 'host', `key ${JSON.stringify(key)} leads with neither rn_1 nor host`).toBe(true);
    }
    expect(keys).not.toContainEqual(runnerLeasesKey('rn_2'));
    expect(keys).not.toContainEqual(runnerDashboardKey('rn_2'));
    expect(keys).not.toContainEqual(runnerChunkDetailKey('rn_2', 'ch_1'));
  });

  it('collapses duplicate keys from multiple frames in one window into a single invalidation', () => {
    const keys = emitAndCollect([
      ['escalation-changed', { runner_id: 'rn_1', chunk_id: 'ch_a', cause: 'opened' }],
      ['takeover-changed', { runner_id: 'rn_1', chunk_id: 'ch_b', takeover_id: 't_1', cause: 'opened' }],
      ['fact-changed', { runner_id: 'rn_1', seq: 3, kind: 'event.recorded', chunk_id: null, lease_id: null }],
    ]);

    const dashboardHits = keys.filter((key) => JSON.stringify(key) === JSON.stringify(runnerDashboardKey('rn_1')));
    expect(dashboardHits).toHaveLength(1);
    expect(keys.filter((key) => JSON.stringify(key) === JSON.stringify(hostKey))).toHaveLength(1);
  });

  it('clears the pending coalesce timer on destroy — no invalidation fires after teardown (review round 4, F2)', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    // A frame lands inside the coalesce window, then the service is torn down before
    // that window's own flush would have fired.
    source.emitNamed('escalation-changed', JSON.stringify({ runner_id: 'rn_1', chunk_id: 'ch_a', cause: 'opened' }), '1');
    TestBed.resetTestingModule();
    invalidate.mockClear();

    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    expect(invalidate).not.toHaveBeenCalled();
  });

  it('flushes a lone event within the coalesce window bound on an otherwise quiet stream (review:F5)', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('escalation-changed', JSON.stringify({ runner_id: 'rn_1', chunk_id: 'ch_a', cause: 'opened' }), '1');

    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS - 1);
    expect(invalidate).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(invalidate.mock.calls.map((call) => call[0]?.queryKey)).toContainEqual(runnerDashboardKey('rn_1'));
  });

  it('re-GETs the whole query client on the confirmed reopen, not on drop detection (D1)', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.hardError();
    vi.advanceTimersByTime(2000);
    TestBed.flushEffects();

    // Still down: no blanket invalidation yet — moving the re-GET back to drop
    // detection would fail this assertion.
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(invalidate.mock.calls.some((call) => call[0] === undefined)).toBe(false);

    FakeEventSource.instances[1].open();
    TestBed.flushEffects();

    // A blanket invalidation (no filter) fires once the reconnect is confirmed.
    expect(invalidate.mock.calls.some((call) => call[0] === undefined)).toBe(true);
  });

  it('routes a stream 401 into the shared recovery seam and schedules no reconnect', () => {
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.authError();
    TestBed.flushEffects();

    expect(recoverSpy).toHaveBeenCalledTimes(1);

    // No reconnect: still the one instance, even well past any backoff window.
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it('is idempotent — a second start() call opens no second connection', () => {
    const live = TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates));
    live.start();
    live.start();

    expect(FakeEventSource.instances).toHaveLength(1);
  });

  describe('the daemon-restart shape: stream 401, session-check fetch fails too', () => {
    beforeEach(() => {
      recoverSpy.mockResolvedValue('read-failed');
    });

    it('re-arms the stream on backoffDelay rather than staying dead or retrying unboundedly', async () => {
      const live = TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates));
      live.start();

      const source = FakeEventSource.instances[0];
      source.open();
      source.authError();
      TestBed.flushEffects();
      await vi.advanceTimersByTimeAsync(0); // let the async onAuthFailed handler settle

      expect(live.authFailed()).toBe(true);
      // Not yet: the re-arm waits out its backoff delay first.
      expect(FakeEventSource.instances).toHaveLength(1);

      await vi.advanceTimersByTimeAsync(backoffDelay(1));
      expect(FakeEventSource.instances).toHaveLength(2);
      expect(live.authFailed()).toBe(false); // a fresh attempt, not the failed one

      FakeEventSource.instances[1].open();
      expect(live.status()).toBe('open');
    });

    it('gives up after STREAM_REARM_MAX_ATTEMPTS, leaving the degraded state standing rather than retrying forever', async () => {
      const live = TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates));
      live.start();

      for (let attempt = 1; attempt <= STREAM_REARM_MAX_ATTEMPTS; attempt++) {
        const source = FakeEventSource.instances[FakeEventSource.instances.length - 1];
        source.open();
        source.authError();
        TestBed.flushEffects();
        await vi.advanceTimersByTimeAsync(0);
        await vi.advanceTimersByTimeAsync(backoffDelay(attempt));
      }

      // Every budgeted attempt is spent: one initial connection plus one per re-arm.
      expect(FakeEventSource.instances).toHaveLength(STREAM_REARM_MAX_ATTEMPTS + 1);

      const last = FakeEventSource.instances[FakeEventSource.instances.length - 1];
      last.open();
      last.authError();
      TestBed.flushEffects();
      await vi.advanceTimersByTimeAsync(0);
      // The cap is spent: advancing well past any backoff opens no further attempt,
      // and the degraded state is left standing for the header to render.
      await vi.advanceTimersByTimeAsync(60_000);

      expect(FakeEventSource.instances).toHaveLength(STREAM_REARM_MAX_ATTEMPTS + 1);
      expect(live.authFailed()).toBe(true);
    });
  });
});

import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  EVENT_SOURCE_FACTORY,
  type EventSourceFactory,
  type FleetEventSource,
  INVALIDATION_COALESCE_WINDOW_MS,
} from 'fleet';
import { vi } from 'vitest';

import { runnerChunkDetailKey, runnerDashboardKey, runnerLeasesKey } from './query-keys';
import { RunnerLiveUpdates } from './runner-live-updates';
import { SessionRecovery } from './session-recovery';

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

describe('RunnerLiveUpdates (blizzard#317 Phase 4)', () => {
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
    recoverSpy = vi.spyOn(recovery, 'recoverFromUnauthenticated').mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('invalidates the leases list, the dashboard, and the named chunk on lease-changed', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed(
      'lease-changed',
      JSON.stringify({ lease_id: 'lease_1', chunk_id: 'ch_1', cause: 'transitioned' }),
      '1',
    );
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(runnerLeasesKey);
    expect(keys).toContainEqual(runnerDashboardKey);
    expect(keys).toContainEqual(runnerChunkDetailKey('ch_1'));
  });

  it('invalidates only the dashboard (and the named chunk) on ask-changed — not the leases list', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed(
      'ask-changed',
      JSON.stringify({ lease_id: 'lease_1', chunk_id: 'ch_2', question_id: 'q_1', cause: 'asked' }),
      '1',
    );
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(runnerDashboardKey);
    expect(keys).toContainEqual(runnerChunkDetailKey('ch_2'));
    expect(keys).not.toContainEqual(runnerLeasesKey);
  });

  it('invalidates the dashboard and the named chunk on escalation-changed', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('escalation-changed', JSON.stringify({ chunk_id: 'ch_3', cause: 'opened' }), '1');
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(runnerDashboardKey);
    expect(keys).toContainEqual(runnerChunkDetailKey('ch_3'));
  });

  it('invalidates the dashboard and the named chunk on takeover-changed', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed(
      'takeover-changed',
      JSON.stringify({ chunk_id: 'ch_4', takeover_id: 'tko_1', cause: 'opened' }),
      '1',
    );
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(runnerDashboardKey);
    expect(keys).toContainEqual(runnerChunkDetailKey('ch_4'));
  });

  it('invalidates the dashboard and the named chunk on environment-changed', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed(
      'environment-changed',
      JSON.stringify({ chunk_id: 'ch_5', environment_id: 'env_1', cause: 'bound' }),
      '1',
    );
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(runnerDashboardKey);
    expect(keys).toContainEqual(runnerChunkDetailKey('ch_5'));
  });

  it('invalidates the dashboard on a runner-wide fact-changed (chunk_id null) with no chunk key', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed(
      'fact-changed',
      JSON.stringify({ seq: 1, kind: 'event.recorded', chunk_id: null, lease_id: null }),
      '1',
    );
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(runnerDashboardKey);
    expect(keys.every((key) => key?.[1] !== 'chunk')).toBe(true);
  });

  it('invalidates the dashboard and the named chunk on a chunk-scoped fact-changed', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed(
      'fact-changed',
      JSON.stringify({ seq: 2, kind: 'chunk-facts-appended', chunk_id: 'ch_6', lease_id: 'lease_2' }),
      '1',
    );
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(runnerDashboardKey);
    expect(keys).toContainEqual(runnerChunkDetailKey('ch_6'));
  });

  it('collapses duplicate keys from multiple frames in one window into a single invalidation (review:F5)', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    // Three frames in the same window, each staling the dashboard key.
    source.emitNamed('escalation-changed', JSON.stringify({ chunk_id: 'ch_a', cause: 'opened' }), '1');
    source.emitNamed('takeover-changed', JSON.stringify({ chunk_id: 'ch_b', takeover_id: 't_1', cause: 'opened' }), '2');
    source.emitNamed(
      'fact-changed',
      JSON.stringify({ seq: 3, kind: 'event.recorded', chunk_id: null, lease_id: null }),
      '3',
    );
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const dashboardHits = invalidate.mock.calls.filter((call) => call[0]?.queryKey === runnerDashboardKey);
    expect(dashboardHits).toHaveLength(1);
  });

  it('clears the pending coalesce timer on destroy — no invalidation fires after teardown (review round 4, F2)', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(RunnerLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    // A frame lands inside the coalesce window, then the service is torn down before
    // that window's own flush would have fired.
    source.emitNamed('escalation-changed', JSON.stringify({ chunk_id: 'ch_a', cause: 'opened' }), '1');
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
    source.emitNamed('escalation-changed', JSON.stringify({ chunk_id: 'ch_a', cause: 'opened' }), '1');

    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS - 1);
    expect(invalidate).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(invalidate.mock.calls.map((call) => call[0]?.queryKey)).toContainEqual(runnerDashboardKey);
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
});

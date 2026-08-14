import { Component, afterRenderEffect, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { hubChunkTranscriptSegmentKey, hubChunkTranscriptsKey } from '../query-keys';
import { EVENT_SOURCE_FACTORY, type EventSourceFactory, type FleetEventSource } from './sse.service';
import { FleetLiveUpdates, INVALIDATION_COALESCE_WINDOW_MS } from './fleet-live';

/** EventSource stand-in with named-listener support — jsdom ships none. */
class FakeEventSource {
  static readonly instances: FakeEventSource[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
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

  close(): void {
    this.readyState = 2;
  }
}

describe('FleetLiveUpdates', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    // Fake timers throughout: dispatch's coalescing window (issue #310) is a real
    // setTimeout, so every test that emits a frame and checks invalidateQueries needs
    // to advance past it.
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
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('invalidates the fleet list, the chunk detail, the queue, the fleet spend read, and the events feed on a chunk-changed event', () => {
    // Usage rides the same fact a chunk-changed reports (issue #60): a chunk's derived
    // cost total and the fleet-wide spend both derive from it, so this event must
    // re-query both, not just status-shaped reads. The events feed unifies open
    // escalations with logged events (blizzard#125 Phase 4), and an escalation
    // surfaces as a chunk-changed frame (status -> needs_human), so this must stale
    // the Events tab too.
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('chunk-changed', JSON.stringify({ chunk_id: 'ch_live', status: 'running' }), '1');
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(['hub', 'chunks']);
    expect(keys).toContainEqual(['hub', 'queue']);
    expect(keys).toContainEqual(['hub', 'chunk', 'ch_live']);
    expect(keys).toContainEqual(['hub', 'fleet-spend']);
    expect(keys).toContainEqual(['hub', 'events']);
  });

  it('collapses duplicate keys from multiple frames in one window into a single invalidation (issue #310)', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    // Two runner-changed frames in the same window both stale ['hub', 'runners'].
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_1', kind: 'paused' }));
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_2', kind: 'resumed' }));
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const registryHits = invalidate.mock.calls.filter((call) => call[0]?.queryKey?.[1] === 'runners');
    expect(registryHits).toHaveLength(1);
  });

  it('preserves distinct keys from different frames in the same window (issue #310)', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('queue-changed', JSON.stringify({}));
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_1', kind: 'paused' }));
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(['hub', 'queue']);
    expect(keys).toContainEqual(['hub', 'runners']);
    expect(keys).toHaveLength(2);
  });

  it('flushes a lone event within the coalesce window bound on an otherwise quiet stream (issue #310)', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('queue-changed', JSON.stringify({}));

    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS - 1);
    expect(invalidate).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(invalidate.mock.calls.map((call) => call[0]?.queryKey)).toContainEqual(['hub', 'queue']);
  });

  it('refetches the transcript-segment index but not an already-fetched final segment’s content on chunk-changed (review:F6)', () => {
    // The index genuinely changes as a chunk's steps progress, so it stays under the
    // `hub/chunk/<id>` prefix a chunk-changed event invalidates. A `final` segment's own
    // content is immutable, and the (chunkId, segmentId) pair already identifies it
    // uniquely — nesting it under the same prefix meant every SSE event on the chunk
    // re-decompressed an already-rendered segment for no reason, defeating this query's
    // own `refetchInterval: false`. Populate the cache with real entries at both keys and
    // assert on TanStack's actual prefix-match invalidation, not just call arguments.
    queryClient.setQueryData(hubChunkTranscriptsKey('ch_live'), { chunk_id: 'ch_live', segments: [] });
    queryClient.setQueryData(hubChunkTranscriptSegmentKey('ch_live', 'sg_1', true), { segment_id: 'sg_1' });
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('chunk-changed', JSON.stringify({ chunk_id: 'ch_live', status: 'running' }), '1');
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    expect(queryClient.getQueryState(hubChunkTranscriptsKey('ch_live'))?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(hubChunkTranscriptSegmentKey('ch_live', 'sg_1', true))?.isInvalidated).toBe(false);
  });

  it('also refetches an open (non-final) segment’s content on chunk-changed (review:F2)', () => {
    // An operator watching a live step needs its content to keep refreshing — a segment
    // the caller hasn't resolved as `final` yet (or has resolved as still open) stays
    // under the `hub/chunk/<id>` prefix so the same signal that refetches the index also
    // refetches it, rather than freezing until an SSE reconnect's blanket invalidation.
    queryClient.setQueryData(hubChunkTranscriptSegmentKey('ch_live', 'sg_2', false), { segment_id: 'sg_2' });
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('chunk-changed', JSON.stringify({ chunk_id: 'ch_live', status: 'running' }), '1');
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    expect(queryClient.getQueryState(hubChunkTranscriptSegmentKey('ch_live', 'sg_2', false))?.isInvalidated).toBe(true);
  });

  it('invalidates the events feed, and that chunk when named, on an event-logged frame', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed(
      'event-logged',
      JSON.stringify({ severity: 'critical', kind: 'escalation-opened', chunk_id: 'ch_live', runner_id: 'rn_1' }),
      '1',
    );
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(['hub', 'events']);
    expect(keys).toContainEqual(['hub', 'chunk', 'ch_live']);
  });

  it('re-reads a chunk on a chunk-changed frame whose status did not move (issue #165)', () => {
    // The client half of the delivery trail's live refresh. `answer.delivered` moves no
    // derived status, so its `chunk-changed` frame repeats the status the board already
    // shows — and this is exactly why the trail needs no event type of its own: dispatch
    // keys off the frame *arriving*, never off the status value differing from the last
    // one, so the chunk read is staled and the dock re-reads the delivered question.
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('chunk-changed', JSON.stringify({ chunk_id: 'ch_live', status: 'running' }), '1');
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);
    invalidate.mockClear();
    // The same status a second time — what a delivery on a running chunk actually sends.
    // A separate window from the first flush, so this frame's keys are not swallowed
    // by the earlier flush's dedup.
    source.emitNamed('chunk-changed', JSON.stringify({ chunk_id: 'ch_live', status: 'running' }), '2');
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(['hub', 'chunk', 'ch_live']);
  });

  it('re-reads the registry on a runner-changed event and the queue on queue-changed', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_1', kind: 'paused', by: 'alice' }));
    source.emitNamed('queue-changed', JSON.stringify({}));
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(['hub', 'runners']);
    expect(keys).toContainEqual(['hub', 'queue']);
  });

  it('re-reads the registry on every runner-changed kind, including the muted ones', () => {
    // Issue #151: muting is the *feed's* concern only. The liveness column refreshes off
    // the heartbeat flood, so a mute that reached dispatch would freeze it. Each kind is
    // emitted in its own coalesce window (issue #310 collapses same-window duplicates),
    // so a per-kind flush still shows up as its own invalidation here.
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_1', kind: 'registered' }));
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_1', kind: 'heartbeat' }));
    vi.advanceTimersByTime(INVALIDATION_COALESCE_WINDOW_MS);

    const registryHits = invalidate.mock.calls.filter((call) => call[0]?.queryKey?.[1] === 'runners');
    expect(registryHits).toHaveLength(2);
  });

  it('keeps the registration and heartbeat kinds out of the event feed', () => {
    // Issue #151: a runner re-registers every pull cycle, so left in the ring these would
    // evict every event an operator actually wants within a few cycles.
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());
    const live = TestBed.inject(FleetLiveUpdates);

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_1', kind: 'registered' }));
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_1', kind: 'heartbeat' }));
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_1', kind: 'paused', by: 'alice' }));
    // A frame from a hub too old to name a kind is news, not noise — it stays.
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_1' }));

    expect(live.log().map((e) => e.data.kind)).toEqual(['paused', undefined]);
  });

  it('accumulates the event feed into the log, oldest first, without touching dispatch', () => {
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());
    const live = TestBed.inject(FleetLiveUpdates);

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('chunk-changed', JSON.stringify({ chunk_id: 'ch_a', status: 'running' }), '1');
    source.emitNamed('queue-changed', JSON.stringify({}), '2');

    const log = live.log();
    expect(log).toHaveLength(2);
    expect(log[0].type).toBe('chunk-changed');
    expect(log[0].data.chunk_id).toBe('ch_a');
    expect(log[1].type).toBe('queue-changed');
    // Monotonic client keys for a stable render track.
    expect(log[1].seq).toBeGreaterThan(log[0].seq);
  });

  it('re-GETs the whole tree after a reconnect to close the gap', () => {
    vi.useFakeTimers();
    try {
      const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
      TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

      const source = FakeEventSource.instances[0];
      source.open();
      source.hardError();
      vi.advanceTimersByTime(2000);
      TestBed.flushEffects();

      // A blanket invalidation (no filter) fires after the reconnect.
      expect(invalidate.mock.calls.some((call) => call[0] === undefined)).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  describe('started from within a reactive context (the app root wiring)', () => {
    // The app root calls `start()` from inside `afterRenderEffect` (`app.ts`), which
    // — like `effect` — tracks signals while its callback runs. `start()` must not
    // throw NG0602 there, and its `handle.reopens()` watcher must still get installed:
    // a hand-built repro (`effect(() => effect(() => {}))`) would catch the NG0602
    // shape but says nothing about whether this app's actual wiring trips it.
    @Component({ selector: 'fleet-test-host', template: '' })
    class Host {
      readonly live = TestBed.inject(FleetLiveUpdates);
      constructor() {
        afterRenderEffect(() => this.live.start());
      }
    }

    it('does not throw NG0602 on render, and the reopens watcher still invalidates the cache', async () => {
      vi.useFakeTimers();
      try {
        const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
        const fixture = TestBed.createComponent(Host);

        expect(() => fixture.detectChanges()).not.toThrow();
        await fixture.whenStable();

        const source = FakeEventSource.instances[0];
        source.open();
        source.hardError();
        vi.advanceTimersByTime(2000);
        TestBed.flushEffects();

        expect(invalidate.mock.calls.some((call) => call[0] === undefined)).toBe(true);
      } finally {
        vi.useRealTimers();
      }
    });
  });
});

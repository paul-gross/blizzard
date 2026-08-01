import { Component, afterRenderEffect, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { EVENT_SOURCE_FACTORY, type EventSourceFactory, type FleetEventSource } from './sse.service';
import { FleetLiveUpdates } from './fleet-live';

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

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(['hub', 'chunks']);
    expect(keys).toContainEqual(['hub', 'queue']);
    expect(keys).toContainEqual(['hub', 'chunk', 'ch_live']);
    expect(keys).toContainEqual(['hub', 'fleet-spend']);
    expect(keys).toContainEqual(['hub', 'events']);
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
    invalidate.mockClear();
    // The same status a second time — what a delivery on a running chunk actually sends.
    source.emitNamed('chunk-changed', JSON.stringify({ chunk_id: 'ch_live', status: 'running' }), '2');

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

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(['hub', 'runners']);
    expect(keys).toContainEqual(['hub', 'queue']);
  });

  it('re-reads the registry on every runner-changed kind, including the muted ones', () => {
    // Issue #151: muting is the *feed's* concern only. The liveness column refreshes off
    // the heartbeat flood, so a mute that reached dispatch would freeze it.
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_1', kind: 'registered' }));
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_1', kind: 'heartbeat' }));

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

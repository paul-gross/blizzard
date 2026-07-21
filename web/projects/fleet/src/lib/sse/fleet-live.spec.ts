import { provideZonelessChangeDetection } from '@angular/core';
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

  it('invalidates the fleet list, the chunk detail, the queue, and the fleet spend read on a chunk-changed event', () => {
    // Usage rides the same fact a chunk-changed reports (issue #60): a chunk's derived
    // cost total and the fleet-wide spend both derive from it, so this event must
    // re-query both, not just status-shaped reads.
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
  });

  it('re-reads the registry on a runner-changed event and the queue on queue-changed', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    TestBed.runInInjectionContext(() => TestBed.inject(FleetLiveUpdates).start());

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('runner-changed', JSON.stringify({ runner_id: 'rn_1' }));
    source.emitNamed('queue-changed', JSON.stringify({}));

    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(['hub', 'runners']);
    expect(keys).toContainEqual(['hub', 'queue']);
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
});

import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import {
  EVENT_SOURCE_FACTORY,
  type EventSourceFactory,
  type FleetEventSource,
  SseService,
  backoffDelay,
  withLastEventId,
} from './sse.service';

/** Minimal transport stand-in — jsdom ships no `EventSource`, and reconnect (plus, since
 * issue #93, the auth-failure channel) must be driven deterministically. */
class FakeEventSource {
  static readonly instances: FakeEventSource[] = [];
  readyState = 0; // CONNECTING
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onautherror: (() => void) | null = null;
  closed = false;
  private readonly listeners = new Map<string, (event: MessageEvent) => void>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, handler: (event: MessageEvent) => void): void {
    this.listeners.set(type, handler);
  }

  open(): void {
    this.readyState = 1; // OPEN
    this.onopen?.();
  }

  emit(data: string, lastEventId = ''): void {
    this.onmessage?.({ data, lastEventId } as MessageEvent);
  }

  emitNamed(type: string, data: string, lastEventId = ''): void {
    this.listeners.get(type)?.({ data, lastEventId } as MessageEvent);
  }

  hardError(): void {
    this.readyState = 2; // CLOSED
    this.onerror?.();
  }

  /** A `401` on this (re)connect attempt — only the fetch-based transport can ever
   * see this in practice; the fake reports it the same way. */
  authError(): void {
    this.readyState = 2; // CLOSED
    this.onautherror?.();
  }

  close(): void {
    this.closed = true;
    this.readyState = 2;
  }
}

describe('SseService', () => {
  beforeEach(() => {
    FakeEventSource.instances.length = 0;
    const factory: EventSourceFactory = (url) => new FakeEventSource(url) as unknown as FleetEventSource;
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), { provide: EVENT_SOURCE_FACTORY, useValue: factory }],
    });
  });

  it('computes exponential backoff clamped to the cap', () => {
    const backoff = { baseMs: 1000, capMs: 30000 };
    expect(backoffDelay(1, backoff)).toBe(1000);
    expect(backoffDelay(2, backoff)).toBe(2000);
    expect(backoffDelay(3, backoff)).toBe(4000);
    expect(backoffDelay(10, backoff)).toBe(30000);
  });

  it('reconnects with backoff after a hard drop', () => {
    vi.useFakeTimers();
    try {
      const handle = TestBed.inject(SseService).connect('/events', { backoff: { baseMs: 10, capMs: 100 } });

      const first = FakeEventSource.instances[0];
      first.open();
      expect(handle.status()).toBe('open');
      expect(FakeEventSource.instances).toHaveLength(1);

      first.hardError();
      expect(handle.status()).toBe('reconnecting');
      expect(handle.reopens()).toBe(1);
      expect(first.closed).toBe(true);

      // No new source is opened until the backoff delay elapses.
      expect(FakeEventSource.instances).toHaveLength(1);
      vi.advanceTimersByTime(10);
      expect(FakeEventSource.instances).toHaveLength(2);

      handle.close();
      expect(handle.status()).toBe('closed');
    } finally {
      vi.useRealTimers();
    }
  });

  it('parses JSON messages onto the stream', () => {
    const handle = TestBed.inject(SseService).connect<{ n: number }>('/events');
    const received: { n: number }[] = [];
    handle.messages.subscribe((message) => received.push(message));

    const source = FakeEventSource.instances[0];
    source.open();
    source.emit(JSON.stringify({ n: 7 }));

    expect(received).toEqual([{ n: 7 }]);
    handle.close();
  });

  it('delivers named events off the events channel', () => {
    const handle = TestBed.inject(SseService).connect<{ chunk_id: string }>('/events', {
      events: ['chunk-changed'],
    });
    const received: { type: string; data: { chunk_id: string } }[] = [];
    handle.events.subscribe((event) => received.push(event));

    const source = FakeEventSource.instances[0];
    source.open();
    source.emitNamed('chunk-changed', JSON.stringify({ chunk_id: 'ch_1' }), '5');

    expect(received).toEqual([{ type: 'chunk-changed', data: { chunk_id: 'ch_1' } }]);
    handle.close();
  });

  it('resumes with last_event_id after a manual reconnect', () => {
    vi.useFakeTimers();
    try {
      const handle = TestBed.inject(SseService).connect('/api/events/stream', {
        backoff: { baseMs: 10, capMs: 100 },
        events: ['chunk-changed'],
      });

      const first = FakeEventSource.instances[0];
      first.open();
      first.emitNamed('chunk-changed', JSON.stringify({ chunk_id: 'ch_1' }), '42');
      first.hardError();
      vi.advanceTimersByTime(10);

      // The re-opened source carries the cursor so the hub replays the gap.
      const second = FakeEventSource.instances[1];
      expect(second.url).toContain('last_event_id=42');

      handle.close();
    } finally {
      vi.useRealTimers();
    }
  });

  it('surfaces authFailed on a 401 and schedules no reconnect (issue #93)', () => {
    vi.useFakeTimers();
    try {
      const handle = TestBed.inject(SseService).connect('/api/events/stream', {
        backoff: { baseMs: 10, capMs: 100 },
      });

      const first = FakeEventSource.instances[0];
      first.open();
      expect(handle.authFailed()).toBe(false);

      first.authError();
      expect(handle.authFailed()).toBe(true);
      expect(handle.status()).toBe('closed');
      // Unlike a hard drop, no reconnect timer is armed — advancing time opens no
      // second source, so a session that will never resolve again is never retried.
      vi.advanceTimersByTime(1000);
      expect(FakeEventSource.instances).toHaveLength(1);

      handle.close();
    } finally {
      vi.useRealTimers();
    }
  });

  it('builds a resume URL that overwrites an existing cursor', () => {
    expect(withLastEventId('/api/events/stream', '9')).toBe('/api/events/stream?last_event_id=9');
    expect(withLastEventId('/api/events/stream?last_event_id=1', '9')).toBe('/api/events/stream?last_event_id=9');
  });
});

import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { EVENT_SOURCE_FACTORY, type EventSourceFactory, SseService, backoffDelay } from './sse.service';

/** Minimal EventSource stand-in — jsdom ships none, and reconnect must be driven deterministically. */
class FakeEventSource {
  static readonly instances: FakeEventSource[] = [];
  readyState = 0; // CONNECTING
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  open(): void {
    this.readyState = 1; // OPEN
    this.onopen?.();
  }

  emit(data: string): void {
    this.onmessage?.({ data } as MessageEvent);
  }

  hardError(): void {
    this.readyState = 2; // CLOSED
    this.onerror?.();
  }

  close(): void {
    this.closed = true;
    this.readyState = 2;
  }
}

describe('SseService', () => {
  beforeEach(() => {
    FakeEventSource.instances.length = 0;
    const factory: EventSourceFactory = (url) => new FakeEventSource(url) as unknown as EventSource;
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
      const handle = TestBed.inject(SseService).connect('/events', { baseMs: 10, capMs: 100 });

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
});

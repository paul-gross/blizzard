import { Injectable, InjectionToken, type Signal, inject, signal } from '@angular/core';
import { Observable, Subject } from 'rxjs';

/**
 * Hand-rolled SSE transport for the fleet's live views (D-097).
 *
 * Native `EventSource` -> RxJS -> signals, with reconnect-then-re-GET gap
 * recovery: on a hard drop the service re-opens with exponential backoff and
 * bumps {@link SseHandle.reopens}, the signal a live view watches to re-GET and
 * close the gap while the stream keeps flowing. This is the SKELETON the design
 * calls for — the transport and reconnect loop are real; higher layers (patching
 * TanStack queries from events) land with the features that stream. When auth
 * arrives (D-018) the `EventSource` seam swaps to a fetch-based source with no
 * change above this service.
 */

/** Connection lifecycle the UI can render (a status dot in the header). */
export type SseStatus = 'idle' | 'open' | 'reconnecting' | 'closed';

/**
 * Factory seam for constructing the underlying `EventSource`. Injected so tests
 * can drive reconnect deterministically with a fake — jsdom ships no
 * `EventSource` — and so the fetch-based transport (D-018) can replace it later.
 */
export type EventSourceFactory = (url: string) => EventSource;
export const EVENT_SOURCE_FACTORY = new InjectionToken<EventSourceFactory>('fleet.EVENT_SOURCE_FACTORY', {
  providedIn: 'root',
  factory: () => (url: string) => new EventSource(url),
});

/** Backoff schedule for reconnect attempts. */
export interface SseBackoff {
  /** Delay before the first reconnect attempt, in ms. */
  readonly baseMs: number;
  /** Ceiling any single delay is clamped to, in ms. */
  readonly capMs: number;
}

const DEFAULT_BACKOFF: SseBackoff = { baseMs: 1000, capMs: 30000 };

/** Exponential backoff, clamped to the cap — a pure function so it is unit-testable. */
export function backoffDelay(attempt: number, backoff: SseBackoff = DEFAULT_BACKOFF): number {
  return Math.min(backoff.capMs, backoff.baseMs * 2 ** Math.max(0, attempt - 1));
}

/** A live handle to one SSE subscription. */
export interface SseHandle<T> {
  /** Current connection lifecycle state. */
  readonly status: Signal<SseStatus>;
  /** Count of reconnects so far — a live view watches this to re-GET after a gap. */
  readonly reopens: Signal<number>;
  /** Parsed message payloads (JSON), in arrival order. */
  readonly messages: Observable<T>;
  /** Close the stream and stop reconnecting. */
  close(): void;
}

@Injectable({ providedIn: 'root' })
export class SseService {
  private readonly factory = inject(EVENT_SOURCE_FACTORY);

  /**
   * Open an SSE stream to `url`. The returned handle exposes the connection
   * status, a reconnect counter, and an Observable of parsed message payloads.
   */
  connect<T = unknown>(url: string, backoff: SseBackoff = DEFAULT_BACKOFF): SseHandle<T> {
    const status = signal<SseStatus>('idle');
    const reopens = signal(0);
    const subject = new Subject<T>();

    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let closed = false;

    const open = (): void => {
      if (closed) return;
      const es = this.factory(url);
      source = es;

      es.onopen = () => {
        attempt = 0;
        status.set('open');
      };
      es.onmessage = (event: MessageEvent) => {
        subject.next(parseEvent<T>(event.data));
      };
      es.onerror = () => {
        // EventSource retries transient blips itself; act only on a hard close.
        if (closed || es.readyState !== EventSourceReadyState.CLOSED) return;
        es.close();
        source = null;
        attempt += 1;
        status.set('reconnecting');
        reopens.update((n) => n + 1);
        timer = setTimeout(open, backoffDelay(attempt, backoff));
      };
    };

    open();

    return {
      status: status.asReadonly(),
      reopens: reopens.asReadonly(),
      messages: subject.asObservable(),
      close: () => {
        closed = true;
        if (timer !== null) clearTimeout(timer);
        source?.close();
        source = null;
        status.set('closed');
        subject.complete();
      },
    };
  }
}

/** `EventSource.CLOSED` as a named constant so the check reads clearly. */
const EventSourceReadyState = { CONNECTING: 0, OPEN: 1, CLOSED: 2 } as const;

function parseEvent<T>(data: string): T {
  try {
    return JSON.parse(data) as T;
  } catch {
    return data as unknown as T;
  }
}

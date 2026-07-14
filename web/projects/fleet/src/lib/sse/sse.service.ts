import { Injectable, InjectionToken, type Signal, inject, signal } from '@angular/core';
import { Observable, Subject } from 'rxjs';

/**
 * Hand-rolled SSE transport for the fleet's live views (D-097).
 *
 * Native `EventSource` -> RxJS -> signals, with reconnect-then-re-GET gap
 * recovery: on a hard drop the service re-opens with exponential backoff and
 * bumps {@link SseHandle.reopens}, the signal a live view watches to re-GET and
 * close the gap while the stream keeps flowing. It also tracks the stream's last
 * event `id` and, on a manual reconnect, hands it back with `?last_event_id=<n>`
 * so the hub replays the buffered gap — belt-and-suspenders alongside the re-GET.
 *
 * The hub's stream emits **named** events (`event: chunk-changed`, etc.); those
 * arrive only on `addEventListener(type, …)`, never `onmessage`, so a caller names
 * the types it wants via {@link SseConnectOptions.events} and reads them off
 * {@link SseHandle.events}. Unnamed frames still surface on {@link SseHandle.messages}.
 *
 * When auth arrives (D-018) the `EventSource` seam swaps to a fetch-based source
 * with no change above this service.
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

/** Options for {@link SseService.connect}. */
export interface SseConnectOptions {
  /** Reconnect backoff schedule; defaults to 1s → 30s exponential. */
  readonly backoff?: SseBackoff;
  /** Named SSE event types to subscribe to (arrive on {@link SseHandle.events}). */
  readonly events?: readonly string[];
}

/** One named SSE frame: its `event:` type and parsed `data:` payload. */
export interface SseEvent<T> {
  readonly type: string;
  readonly data: T;
}

/** A live handle to one SSE subscription. */
export interface SseHandle<T> {
  /** Current connection lifecycle state. */
  readonly status: Signal<SseStatus>;
  /** Count of reconnects so far — a live view watches this to re-GET after a gap. */
  readonly reopens: Signal<number>;
  /** Parsed payloads of **unnamed** (`message`) frames, in arrival order. */
  readonly messages: Observable<T>;
  /** Parsed **named** frames (the type the caller subscribed to), in arrival order. */
  readonly events: Observable<SseEvent<T>>;
  /** Close the stream and stop reconnecting. */
  close(): void;
}

/** Append or replace a `last_event_id` query param so a reconnect resumes the stream. */
export function withLastEventId(url: string, lastEventId: string): string {
  const [path, query = ''] = url.split('?');
  const params = new URLSearchParams(query);
  params.set('last_event_id', lastEventId);
  return `${path}?${params.toString()}`;
}

@Injectable({ providedIn: 'root' })
export class SseService {
  private readonly factory = inject(EVENT_SOURCE_FACTORY);

  /**
   * Open an SSE stream to `url`. The returned handle exposes the connection
   * status, a reconnect counter, and observables of unnamed and named payloads.
   */
  connect<T = unknown>(url: string, options: SseConnectOptions = {}): SseHandle<T> {
    const backoff = options.backoff ?? DEFAULT_BACKOFF;
    const eventTypes = options.events ?? [];
    const status = signal<SseStatus>('idle');
    const reopens = signal(0);
    const messages = new Subject<T>();
    const events = new Subject<SseEvent<T>>();

    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let closed = false;
    let lastEventId: string | null = null;

    const open = (): void => {
      if (closed) return;
      // Resume from where the stream left off after a manual reconnect (the hub
      // replays buffered events with id > cursor), then live-streams again.
      const target = lastEventId === null ? url : withLastEventId(url, lastEventId);
      const es = this.factory(target);
      source = es;

      es.onopen = () => {
        attempt = 0;
        status.set('open');
      };
      es.onmessage = (event: MessageEvent) => {
        if (event.lastEventId) lastEventId = event.lastEventId;
        messages.next(parseEvent<T>(event.data));
      };
      for (const type of eventTypes) {
        es.addEventListener(type, (event: MessageEvent) => {
          if (event.lastEventId) lastEventId = event.lastEventId;
          events.next({ type, data: parseEvent<T>(event.data) });
        });
      }
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
      messages: messages.asObservable(),
      events: events.asObservable(),
      close: () => {
        closed = true;
        if (timer !== null) clearTimeout(timer);
        source?.close();
        source = null;
        status.set('closed');
        messages.complete();
        events.complete();
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

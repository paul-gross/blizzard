import { Injectable, InjectionToken, type Signal, inject, signal } from '@angular/core';
import { Observable, Subject } from 'rxjs';

/**
 * Hand-rolled SSE transport for the fleet's live views.
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
 * Now that auth has arrived (issue #93), the default factory is the **fetch-based**
 * transport ({@link fetchEventSourceFactory}) rather than native `EventSource`:
 * `EventSource` sends no cookie-auth-aware status to script (it exposes no response
 * code at all), so a session expiring mid-stream would otherwise read as an
 * indistinguishable transient blip and retry forever. The fetch-based source detects
 * a `401` specifically and reports it through {@link SseHandle.authFailed} instead of
 * scheduling a reconnect — no change to any caller above this service.
 */

/** Connection lifecycle the UI can render (a status dot in the header). */
export type SseStatus = 'idle' | 'open' | 'reconnecting' | 'closed';

/**
 * The narrow surface {@link SseService} needs from its underlying transport — native
 * `EventSource` satisfies this structurally (unused members are simply ignored), and
 * {@link FetchEventSource} is the fetch-based implementation. `onautherror` is the one
 * member `EventSource` never calls (it cannot: it has no access to the response
 * status) — declared optional so `EventSource`'s own type, which does not declare it
 * at all, still satisfies this interface.
 */
export interface FleetEventSource {
  onopen: (() => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onerror: (() => void) | null;
  /** Called once, instead of {@link onerror}, when the stream closed because the
   * server answered `401` — a distinct terminal condition from a transient drop. */
  onautherror?: (() => void) | null;
  readonly readyState: number;
  addEventListener(type: string, listener: (event: MessageEvent) => void): void;
  close(): void;
}

/**
 * Factory seam for constructing the underlying transport. Injected so tests
 * can drive reconnect deterministically with a fake — jsdom ships no
 * `EventSource` — and so the transport can be swapped without touching
 * {@link SseService} itself.
 */
export type EventSourceFactory = (url: string) => FleetEventSource;
export const EVENT_SOURCE_FACTORY = new InjectionToken<EventSourceFactory>('fleet.EVENT_SOURCE_FACTORY', {
  providedIn: 'root',
  factory: () => fetchEventSourceFactory,
});

/** `SSE` frame parse result: `data` is `undefined` for a comment-only/keepalive
 * frame (no `data:` line), which the reader skips rather than emitting. */
interface ParsedFrame {
  readonly event?: string;
  readonly data?: string;
  readonly id?: string;
}

function parseSseFrame(raw: string): ParsedFrame {
  let event: string | undefined;
  const dataLines: string[] = [];
  let id: string | undefined;
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice('event:'.length).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice('data:'.length).replace(/^ /, ''));
    else if (line.startsWith('id:')) id = line.slice('id:'.length).trim();
  }
  return { event, data: dataLines.length > 0 ? dataLines.join('\n') : undefined, id };
}

/**
 * The fetch-based `EventSource` counterpart (issue #93): reads the stream as a raw
 * `fetch` response body so a `401` (an expired/absent session — `EventSource` cannot
 * see this at all) is detectable and reported via {@link FleetEventSource.onautherror}
 * rather than folded into the generic {@link FleetEventSource.onerror} a transient
 * network blip also uses. `credentials: 'include'` carries the session cookie exactly
 * as the browser did for `EventSource` automatically.
 */
export class FetchEventSource implements FleetEventSource {
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onautherror: (() => void) | null = null;
  readyState = 0;

  private readonly listeners = new Map<string, Set<(event: MessageEvent) => void>>();
  private readonly controller = new AbortController();
  private closed = false;

  constructor(private readonly url: string) {
    void this.run();
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.readyState = 2;
    this.controller.abort();
  }

  private async run(): Promise<void> {
    let response: Response;
    try {
      response = await fetch(this.url, {
        headers: { accept: 'text/event-stream' },
        credentials: 'include',
        signal: this.controller.signal,
      });
    } catch {
      if (!this.closed) this.fail();
      return;
    }
    if (this.closed) return;
    if (response.status === 401) {
      this.readyState = 2;
      this.onautherror?.();
      return;
    }
    if (!response.ok || response.body === null) {
      this.fail();
      return;
    }
    this.readyState = 1;
    this.onopen?.();

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let lastEventId: string | undefined;
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep = buffer.indexOf('\n\n');
        while (sep !== -1) {
          const raw = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          const frame = parseSseFrame(raw);
          if (frame.id) lastEventId = frame.id;
          if (frame.data !== undefined) {
            const event = { data: frame.data, lastEventId: lastEventId ?? '' } as MessageEvent;
            if (frame.event && frame.event !== 'message') {
              for (const listener of this.listeners.get(frame.event) ?? []) listener(event);
            } else {
              this.onmessage?.(event);
            }
          }
          sep = buffer.indexOf('\n\n');
        }
      }
    } catch {
      if (this.closed) return;
      this.fail();
      return;
    }
    if (!this.closed) this.fail();
  }

  private fail(): void {
    this.readyState = 2;
    this.onerror?.();
  }
}

/** The default {@link EventSourceFactory} — one {@link FetchEventSource} per `connect`. */
export function fetchEventSourceFactory(url: string): FleetEventSource {
  return new FetchEventSource(url);
}

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
  /** `true` once the stream closed on a `401` (issue #93) — a session that expired
   * mid-stream, distinct from a transient drop (which keeps retrying instead). Set
   * at most once; no further reconnect is scheduled once this flips. A consumer
   * (the app root) watches this to route to `/login` within the one reconnect cycle
   * that surfaced it, rather than an unbounded retry loop. */
  readonly authFailed: Signal<boolean>;
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
    const authFailed = signal(false);
    const messages = new Subject<T>();
    const events = new Subject<SseEvent<T>>();

    let source: FleetEventSource | null = null;
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
      // Only the fetch-based transport ever calls this (native `EventSource` cannot
      // see a status code at all) — a `401` is terminal: no reconnect is scheduled,
      // so a session that expired mid-stream surfaces once, on this one reconnect
      // cycle, rather than retrying against a session that will never resolve again.
      es.onautherror = () => {
        if (closed) return;
        es.close();
        source = null;
        status.set('closed');
        authFailed.set(true);
      };
    };

    open();

    return {
      status: status.asReadonly(),
      reopens: reopens.asReadonly(),
      authFailed: authFailed.asReadonly(),
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

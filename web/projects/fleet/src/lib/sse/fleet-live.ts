import { DestroyRef, EnvironmentInjector, Injectable, type Signal, effect, inject, signal } from '@angular/core';
import { QueryClient } from '@tanstack/angular-query-experimental';

import { hubChunkKey, hubChunksKey, hubFleetSpendKey, hubQuestionsKey, hubQueueKey, hubRunnersKey } from '../query-keys';
import { type SseHandle, type SseStatus, SseService } from './sse.service';

/** The hub's SSE stream endpoint (deliberately not in OpenAPI — native EventSource). */
export const HUB_EVENT_STREAM_URL = '/api/events/stream';

/** The named event types the hub broadcasts. */
export const HUB_EVENT_TYPES = [
  'chunk-changed',
  'question-asked',
  'question-answered',
  'decision-opened',
  'decision-resolved',
  'queue-changed',
  'runner-changed',
] as const;

/** The payloads carried by each hub event frame. */
interface ChunkChanged {
  chunk_id: string;
  status: string;
}
interface QuestionEvent {
  chunk_id: string;
  question_id: string;
}
interface DecisionEvent {
  chunk_id: string;
  decision_id: string;
}
interface RunnerEvent {
  runner_id: string;
}
type HubEventPayload = Partial<ChunkChanged & QuestionEvent & DecisionEvent & RunnerEvent>;

/** One of the named event types the hub broadcasts ({@link HUB_EVENT_TYPES}). */
export type HubEventType = (typeof HUB_EVENT_TYPES)[number];

/** A chunk-changed frame invalidates the fleet list, the ready queue (a status flip
 * can add or remove a chunk from it), that chunk's own detail when the payload names
 * one, and the fleet spend-since read: usage rides the same fact a chunk-changed
 * reports (issue #60), so a chunk's derived cost total and the fleet-wide spend both
 * derive from it — the prefix key closes every cached window. */
function chunkChangedKeys(data: HubEventPayload): readonly (readonly unknown[])[] {
  return [hubChunksKey, hubQueueKey, ...(data.chunk_id ? [hubChunkKey(data.chunk_id)] : []), hubFleetSpendKey];
}

/** A question-asked/-answered frame invalidates the fleet-wide ask list (the right
 * rail surfaces an ask on a chunk nobody has selected, so it cannot ride on the
 * chunk's own detail read), the fleet list, and that chunk's detail when named —
 * both flip the derived status to/from `waiting_on_human`. */
function chunkQuestionKeys(data: HubEventPayload): readonly (readonly unknown[])[] {
  return [hubQuestionsKey, hubChunksKey, ...(data.chunk_id ? [hubChunkKey(data.chunk_id)] : [])];
}

/** A decision-opened/-resolved frame invalidates the fleet list and that chunk's
 * detail when named — same status-flip reasoning as {@link chunkQuestionKeys}. */
function chunkDecisionKeys(data: HubEventPayload): readonly (readonly unknown[])[] {
  return [hubChunksKey, ...(data.chunk_id ? [hubChunkKey(data.chunk_id)] : [])];
}

/**
 * The event → query-key invalidation registry (issue #82) — the single place a live
 * event names what it stales, so wiring a new live feature into the SSE spine is
 * adding a row here, not a `case` in {@link FleetLiveUpdates.dispatch}. Exhaustive
 * over {@link HubEventType} (a compile-time guard, same intent as `STATUS_LANE`): a
 * new event type added to {@link HUB_EVENT_TYPES} is then a compile error here until
 * it is given a row, instead of silently dispatching to nothing.
 */
const EVENT_INVALIDATION_REGISTRY: Record<HubEventType, (data: HubEventPayload) => readonly (readonly unknown[])[]> = {
  'chunk-changed': chunkChangedKeys,
  'question-asked': chunkQuestionKeys,
  'question-answered': chunkQuestionKeys,
  'decision-opened': chunkDecisionKeys,
  'decision-resolved': chunkDecisionKeys,
  'queue-changed': () => [hubQueueKey],
  'runner-changed': () => [hubRunnersKey],
};

/**
 * One event recorded for the Event log feed (issue #25): its stream arrival order
 * (`seq` — a stable, monotonic client key), its board vocabulary `type`, the parsed
 * `data`, and the client-side arrival time `at` (ms epoch; the hub frames carry no
 * timestamp of their own). Presentation — the human-readable summary — is the panel's.
 */
export interface LoggedEvent {
  readonly seq: number;
  readonly type: string;
  readonly data: HubEventPayload;
  readonly at: number;
}

/**
 * Recent-event ring cap for the Event log — matches the broker's history depth
 * (events/broker.py, `history=256`) so the feed holds as much as a fresh connect can
 * ever backfill, and no more.
 */
const LOG_LIMIT = 256;

/**
 * The live-update spine of the board: one SSE subscription to the hub's
 * event stream that **invalidates or patches TanStack queries** so every live view
 * keeps streaming while the cache stays truthful. It is the sanctioned bridge from
 * the {@link SseService} transport to the query cache — the one place SSE meets reads.
 *
 * `dispatch` is a lookup into {@link EVENT_INVALIDATION_REGISTRY}, not a per-event
 * branch — see that registry's doc for what each event type stales.
 *
 * Gap recovery is reconnect-then-re-GET: on every reconnect the service invalidates
 * the whole `hub` tree, so any events missed while the socket was down are closed by
 * a fresh read — and the transport also resumes with `last_event_id` for the replay.
 *
 * It also tees the same event feed into {@link log}, a bounded ring the Event log panel
 * renders (issue #25): because the same single subscription records every frame, the
 * broker's connect-time replay (its buffered history) lands in the log as backfill for
 * free, and the query-invalidation dispatch stays exactly as it was.
 */
@Injectable({ providedIn: 'root' })
export class FleetLiveUpdates {
  private readonly sse = inject(SseService);
  private readonly queryClient = inject(QueryClient);
  private readonly injector = inject(EnvironmentInjector);
  private readonly destroyRef = inject(DestroyRef);
  private handle: SseHandle<HubEventPayload> | null = null;
  private seq = 0;
  private readonly _log = signal<readonly LoggedEvent[]>([]);

  /** Connection lifecycle for the header status, or `idle` before {@link start}. */
  get status(): Signal<SseStatus> {
    return this.handle?.status ?? IDLE_STATUS;
  }

  /** `true` once the stream closed on a `401` (issue #93) — a session that expired
   * mid-stream. The app root watches this and routes to `/login`; `false` before
   * {@link start} and for the whole life of a stream that never sees one. */
  get authFailed(): Signal<boolean> {
    return this.handle?.authFailed ?? FALSE_STATUS;
  }

  /**
   * The recent-event feed for the Event log (issue #25), oldest → newest, capped at
   * {@link LOG_LIMIT}. Empty before {@link start}; the panel reverses it for display.
   */
  get log(): Signal<readonly LoggedEvent[]> {
    return this._log.asReadonly();
  }

  /**
   * Open the live stream and wire it to the query cache. Idempotent — a second call
   * is a no-op. Auto-closes on the caller's {@link DestroyRef} (the app teardown).
   */
  start(): void {
    if (this.handle) return;
    const handle = this.sse.connect<HubEventPayload>(HUB_EVENT_STREAM_URL, {
      events: [...HUB_EVENT_TYPES],
    });
    this.handle = handle;

    const sub = handle.events.subscribe(({ type, data }) => {
      this.record(type, data);
      this.dispatch(type, data);
    });

    // Reconnect-then-re-GET: a fresh reconnect re-reads the whole tree to close any gap.
    let lastReopens = handle.reopens();
    const ref = effect(
      () => {
        const reopens = handle.reopens();
        if (reopens > lastReopens) {
          lastReopens = reopens;
          void this.queryClient.invalidateQueries();
        }
      },
      { injector: this.injector },
    );

    this.destroyRef.onDestroy(() => {
      sub.unsubscribe();
      ref.destroy();
      handle.close();
      this.handle = null;
    });
  }

  /** Append one frame to the bounded Event log ring, dropping the oldest past the cap. */
  private record(type: string, data: HubEventPayload): void {
    const entry: LoggedEvent = { seq: ++this.seq, type, data, at: Date.now() };
    this._log.update((prev) => {
      const next = [...prev, entry];
      return next.length > LOG_LIMIT ? next.slice(next.length - LOG_LIMIT) : next;
    });
  }

  private dispatch(type: string, data: HubEventPayload): void {
    const keys = EVENT_INVALIDATION_REGISTRY[type as HubEventType]?.(data) ?? [];
    for (const queryKey of keys) {
      void this.queryClient.invalidateQueries({ queryKey });
    }
  }
}

/** A frozen `idle` status used before the stream is opened. */
const IDLE_STATUS: Signal<SseStatus> = (() => {
  const s = () => 'idle' as const;
  return s as Signal<SseStatus>;
})();

/** A frozen `false` used for {@link FleetLiveUpdates.authFailed} before the stream is opened. */
const FALSE_STATUS: Signal<boolean> = (() => {
  const s = () => false;
  return s as Signal<boolean>;
})();

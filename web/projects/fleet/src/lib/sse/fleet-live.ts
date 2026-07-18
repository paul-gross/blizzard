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
 * - a `chunk-changed` invalidates the fleet list, that chunk's detail, and the queue
 *   (a status flip can add or remove a chunk from the ready queue), plus the fleet
 *   spend-since read (issue #60) — a chunk's derived cost total and the fleet-wide
 *   spend both derive from the same usage facts, so a chunk-changed re-queries both;
 * - question/decision events invalidate that chunk's detail and the list (they flip
 *   the derived status to/from `waiting_on_human`);
 * - `queue-changed` re-peeks the queue; `runner-changed` re-reads the registry.
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
    const invalidate = (queryKey: readonly unknown[]): void => {
      void this.queryClient.invalidateQueries({ queryKey });
    };
    switch (type) {
      case 'chunk-changed':
        invalidate(hubChunksKey);
        invalidate(hubQueueKey);
        if (data.chunk_id) invalidate(hubChunkKey(data.chunk_id));
        // Usage rides the same fact, so a chunk's cost total and the fleet-wide spend
        // derive from it too (issue #60) — the prefix key closes every cached window.
        invalidate(hubFleetSpendKey);
        break;
      case 'question-asked':
      case 'question-answered':
        // The fleet-wide ask list too: the right rail surfaces an ask on a chunk
        // nobody has selected, so it cannot ride on the chunk's own detail read.
        invalidate(hubQuestionsKey);
        invalidate(hubChunksKey);
        if (data.chunk_id) invalidate(hubChunkKey(data.chunk_id));
        break;
      case 'decision-opened':
      case 'decision-resolved':
        invalidate(hubChunksKey);
        if (data.chunk_id) invalidate(hubChunkKey(data.chunk_id));
        break;
      case 'queue-changed':
        invalidate(hubQueueKey);
        break;
      case 'runner-changed':
        invalidate(hubRunnersKey);
        break;
      default:
        break;
    }
  }
}

/** A frozen `idle` status used before the stream is opened. */
const IDLE_STATUS: Signal<SseStatus> = (() => {
  const s = () => 'idle' as const;
  return s as Signal<SseStatus>;
})();

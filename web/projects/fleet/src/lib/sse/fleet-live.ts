import { DestroyRef, EnvironmentInjector, Injectable, type Signal, inject, signal } from '@angular/core';
import { QueryClient } from '@tanstack/angular-query-experimental';

import {
  ActivityChunkChangeCause,
  type ActivityView,
  ChunkStatus,
  type ChunkChangedPayload,
  type DecisionOpenedPayload,
  type DecisionResolvedPayload,
  type EventLoggedPayload,
  HubEventType,
  type QuestionAnsweredPayload,
  type QuestionAskedPayload,
  type QueueChangedPayload,
  RunnerChangeKind,
  type RunnerChangedPayload,
} from '../api/hub';
import {
  hubBacklogKey,
  hubChunkKey,
  hubChunksKey,
  hubEventsKey,
  hubFleetSpendKey,
  hubQuestionsKey,
  hubDecisionsKey,
  hubQueueKey,
  hubRunnersKey,
} from '../core/query-keys';
import { LiveInvalidationSpine } from './live-invalidation-spine';
import { type SseStatus, SseService } from './sse.service';

export type { RunnerChangeKind };

/** The hub's SSE stream endpoint (deliberately not in OpenAPI — native EventSource). */
export const HUB_EVENT_STREAM_URL = '/api/events/stream';

/**
 * How long {@link LiveInvalidationSpine.dispatch} accumulates invalidation keys before
 * flushing them as one pass. A burst of frames — a batch promote, a
 * runner claiming several chunks — would otherwise fire one `invalidateQueries` call
 * per key per frame; this window lets the overlapping key sets from a burst collapse
 * into a single pass instead. 250ms: short enough that a quiet stream's lone event
 * still reads as prompt, long enough that same-tick bursts (the common case for a
 * hub emitting several events off one transition) actually overlap in the window.
 */
export const INVALIDATION_COALESCE_WINDOW_MS = 250;

/** The named event types the hub broadcasts, in the generated {@link HubEventType}'s order. */
export const HUB_EVENT_TYPES: readonly HubEventType[] = Object.values(HubEventType);

/**
 * Each hub frame type's generated payload. Optional fields are present-when-meaningful
 * (a chunk that has never transitioned carries no `prev_node`); `key` is the
 * fact-identity stamp — the merge/dedup key a backfilled row and the live frame
 * reporting the same fact share — omitted on a frame with no durable fact behind it
 * (`queue-changed`, a `registered`/`heartbeat` `runner-changed`, an idempotent no-op)
 * or from a hub that predates the stamp.
 */
export interface HubEventPayloads {
  [HubEventType.CHUNK_CHANGED]: ChunkChangedPayload;
  [HubEventType.QUESTION_ASKED]: QuestionAskedPayload;
  [HubEventType.QUESTION_ANSWERED]: QuestionAnsweredPayload;
  [HubEventType.DECISION_OPENED]: DecisionOpenedPayload;
  [HubEventType.DECISION_RESOLVED]: DecisionResolvedPayload;
  [HubEventType.QUEUE_CHANGED]: QueueChangedPayload;
  [HubEventType.RUNNER_CHANGED]: RunnerChangedPayload;
  [HubEventType.EVENT_LOGGED]: EventLoggedPayload;
}

/** Any hub frame's payload — which member is fixed by the frame type it arrived under. */
export type HubEventPayload = HubEventPayloads[HubEventType];

/** A `chunk-changed` frame's payload. */
export type ChunkChanged = ChunkChangedPayload;
/** A `question-asked`/`question-answered` frame's payload. */
export type QuestionEvent = QuestionAskedPayload | QuestionAnsweredPayload;
/** A `decision-opened`/`decision-resolved` frame's payload. */
export type DecisionEvent = DecisionOpenedPayload | DecisionResolvedPayload;
/** A `runner-changed` frame's payload. */
export type RunnerEvent = RunnerChangedPayload;
/** An `event-logged` frame's payload. */
export type EventLoggedEvent = EventLoggedPayload;
/** The fact-identity stamp every keyed frame carries ({@link HubEventPayloads}). */
export type KeyedEvent = Pick<ChunkChangedPayload, 'key'>;

/**
 * The one shape the Activity feed reads a frame through, live or backfilled: the
 * activity read's row (`ActivityView`) less its envelope (`at`, `type`, `key`). Every
 * {@link HubEventPayload} member is assignable to it, so the live tee stores frames
 * unchanged, and a backfilled row — which may lack `status`, or carry a `cause` or
 * `kind` from a newer hub — fits without narrowing.
 */
export type LoggedEventData = Omit<ActivityView, 'at' | 'type' | 'key'> & { readonly key?: string | null };

/**
 * The general rule behind every Activity feed drop: a frame belongs in the ring only when
 * its kind carries a durable fact behind it. `queue-changed` reports a reorder with no
 * per-chunk row behind it (`bzh:ranking-is-per-list`) — never a fact, so it is dropped
 * whole, unconditionally, whichever hub sent it. {@link MUTED_RUNNER_KINDS} is the same
 * rule applied within `runner-changed`, whose frames are factual for most kinds
 * (`paused`, `resumed`, …) but not for these.
 *
 * The test is "this kind has no durable fact", never "this frame has no `key`" — `key`'s
 * absence is ambiguous (it also marks a frame from a hub older than this stamp,
 * which the ring deliberately keeps) and is evidence only, not the predicate itself.
 * Dropping is scoped to the feed either way: {@link LiveInvalidationSpine.dispatch}
 * still invalidates on every dropped frame, keyed off `type` alone.
 */
const NO_DURABLE_FACT_TYPES: ReadonlySet<string> = new Set<HubEventType>([HubEventType.QUEUE_CHANGED]);

/**
 * The `runner-changed` kinds the Activity feed drops — {@link
 * NO_DURABLE_FACT_TYPES}'s rule applied within this one type. A runner re-registers on
 * every pull-loop cycle as its liveness heartbeat, so these two are the overwhelming
 * majority of all frames and carry no news an operator can act on — left in, they would
 * evict every other event out of the {@link LOG_LIMIT} ring within a few cycles, so this
 * is what keeps the feed legible rather than merely tidier. Dropping is scoped to the
 * feed: {@link LiveInvalidationSpine.dispatch} still invalidates on them, so queries
 * keyed on runner liveness keep refreshing on every heartbeat.
 *
 * `external-usage` is muted for a different reason: it is not an
 * operator-visible activity-feed entry, and carries no `key` — there is no fact-table row
 * identity worth naming, only an advisory display field the fleet registry re-reads.
 */
const MUTED_RUNNER_KINDS: ReadonlySet<string> = new Set<RunnerChangeKind>([
  RunnerChangeKind.REGISTERED,
  RunnerChangeKind.HEARTBEAT,
  RunnerChangeKind.EXTERNAL_USAGE,
]);

/** The causes the hub's activity read backfills from its chunk fact sources — the wire's own
 * {@link ActivityChunkChangeCause}. A chunk-scoped telemetry fact can repeat the last
 * transition's status and key; neither makes that fact another activity occurrence. */
const ACTIVITY_CHUNK_CAUSES: ReadonlySet<string> = new Set<string>(Object.values(ActivityChunkChangeCause));

/** Whether a frame belongs in the Activity feed — see {@link NO_DURABLE_FACT_TYPES} and
 * {@link MUTED_RUNNER_KINDS}. */
function isLoggable(type: string, data: LoggedEventData): boolean {
  if (NO_DURABLE_FACT_TYPES.has(type)) return false;
  if (type === HubEventType.CHUNK_CHANGED) return ACTIVITY_CHUNK_CAUSES.has(data.cause ?? '');
  return type !== HubEventType.RUNNER_CHANGED || !MUTED_RUNNER_KINDS.has(data.kind ?? '');
}

/** The named chunk's own detail key, when the frame names a chunk. */
function chunkDetailKeys(data: HubEventPayload): readonly (readonly unknown[])[] {
  return 'chunk_id' in data && data.chunk_id ? [hubChunkKey(data.chunk_id)] : [];
}

/** A chunk-changed frame invalidates the fleet list, the ready queue (a status flip
 * can add or remove a chunk from it), that chunk's own detail when the payload names
 * one, and the fleet spend-since read: usage rides the same fact a chunk-changed
 * reports, so a chunk's derived cost total and the fleet-wide spend both
 * derive from it — the prefix key closes every cached window. It also stales the
 * Events tab's feed: an escalation surfaces as a `chunk-changed` frame (status flips
 * to `needs_human`), and the feed unifies open escalations with logged events, so a
 * status flip that carries an escalation must re-read it too. */
function chunkChangedKeys(data: HubEventPayload): readonly (readonly unknown[])[] {
  return [
    hubChunksKey,
    hubQueueKey,
    ...chunkDetailKeys(data),
    hubFleetSpendKey,
    hubEventsKey,
    ...(chunkEnded(data) ? [hubQuestionsKey, hubDecisionsKey] : []),
  ];
}

/** Whether a chunk-changed frame reports the chunk ended (`stopped`/`done`). The hub lists
 * no open question or decision on an ended chunk, so the fleet-wide rails must re-read
 * at once rather than wait on their poll backstop. Other frames leave both lists alone. */
function chunkEnded(data: HubEventPayload): boolean {
  const status = 'status' in data ? data.status : undefined;
  return status === ChunkStatus.STOPPED || status === ChunkStatus.DONE;
}

/** A question-asked/-answered frame invalidates the fleet-wide ask list (the right
 * rail surfaces an ask on a chunk nobody has selected, so it cannot ride on the
 * chunk's own detail read), the fleet list, and that chunk's detail when named —
 * both flip the derived status to/from `waiting_on_human`. */
function chunkQuestionKeys(data: HubEventPayload): readonly (readonly unknown[])[] {
  return [hubQuestionsKey, hubChunksKey, ...chunkDetailKeys(data)];
}

/** A decision-opened/-resolved frame invalidates the fleet list, the fleet-wide
 * open-decision list, and that chunk's detail when named — same status-flip
 * reasoning as {@link chunkQuestionKeys}. */
function chunkDecisionKeys(data: HubEventPayload): readonly (readonly unknown[])[] {
  return [hubChunksKey, hubDecisionsKey, ...chunkDetailKeys(data)];
}

/**
 * The event → query-key invalidation registry — the single place a live
 * event names what it stales, so wiring a new live feature into the SSE spine is
 * adding a row here, not a `case` in {@link LiveInvalidationSpine.dispatch}. Exhaustive
 * over {@link HubEventType} (a compile-time guard, same intent as `STATUS_LANE`): a
 * new event type added to the generated vocabulary is then a compile error here until
 * it is given a row, instead of silently dispatching to nothing.
 */
const EVENT_INVALIDATION_REGISTRY: Record<HubEventType, (data: HubEventPayload) => readonly (readonly unknown[])[]> = {
  [HubEventType.CHUNK_CHANGED]: chunkChangedKeys,
  [HubEventType.QUESTION_ASKED]: chunkQuestionKeys,
  [HubEventType.QUESTION_ANSWERED]: chunkQuestionKeys,
  [HubEventType.DECISION_OPENED]: chunkDecisionKeys,
  [HubEventType.DECISION_RESOLVED]: chunkDecisionKeys,
  // A backlog reorder fires the same event as a ready-queue one (no distinct
  // payload key, `bzh:ranking-is-per-list`), so both cached orders invalidate.
  [HubEventType.QUEUE_CHANGED]: () => [hubQueueKey, hubBacklogKey],
  [HubEventType.RUNNER_CHANGED]: () => [hubRunnersKey],
  [HubEventType.EVENT_LOGGED]: (data) => [hubEventsKey, ...chunkDetailKeys(data)],
};

/**
 * One event recorded for the Activity feed: its stream arrival order
 * (`seq` — a stable, monotonic client key), its board vocabulary `type`, the parsed
 * `data`, and the client-side arrival time `at` (ms epoch; the hub frames carry no
 * timestamp of their own). `type` stays a plain string: a backfilled row from a newer
 * hub may name a type this client does not know, and the feed renders it raw.
 *
 * `key` is `data.key` ({@link KeyedEvent}), lifted to the top level. Absent on a frame
 * from a hub that predates this stamp.
 */
export interface LoggedEvent {
  readonly seq: number;
  readonly type: string;
  readonly data: LoggedEventData;
  readonly at: number;
  readonly key?: string;
}

/**
 * Recent-event ring cap for *this live tee alone* — matches the broker's replay history
 * depth so the ring never holds more than a fresh connect's
 * own replay tail could ever deliver. It bounds only this ring, not what any
 * consumer renders.
 */
const LOG_LIMIT = 256;

/**
 * The live-update spine of the board: one SSE subscription to the hub's event stream
 * that **invalidates or patches TanStack queries** so every live view keeps streaming
 * while the cache stays truthful. It is the sanctioned bridge from the
 * {@link SseService} transport to the query cache — the one place SSE meets reads.
 *
 * The coalescing dispatch and reconnect-then-re-GET gap recovery are
 * {@link LiveInvalidationSpine}'s, configured here with
 * {@link EVENT_INVALIDATION_REGISTRY} — see that registry's doc for what each event
 * type stales. What stays here is what only the hub's own service does: tee the same
 * event feed into {@link log}, a bounded ring the Activity feed panel renders.
 * Because the spine's `onFrame` hook records every frame before dispatch runs, the
 * broker's connect-time replay (its buffered history) lands in the log as backfill for
 * free. The tee is where the feed's noise floor is set — {@link isLoggable} mutes
 * frames an operator cannot act on, and only there, never on the dispatch side.
 */
@Injectable({ providedIn: 'root' })
export class FleetLiveUpdates {
  private readonly sse = inject(SseService);
  private readonly queryClient = inject(QueryClient);
  private readonly injector = inject(EnvironmentInjector);
  private readonly destroyRef = inject(DestroyRef);
  private seq = 0;
  private readonly _log = signal<readonly LoggedEvent[]>([]);
  private readonly spine = new LiveInvalidationSpine<HubEventPayload, HubEventType>({
    sse: this.sse,
    queryClient: this.queryClient,
    injector: this.injector,
    destroyRef: this.destroyRef,
    streamUrl: HUB_EVENT_STREAM_URL,
    eventTypes: HUB_EVENT_TYPES,
    registry: EVENT_INVALIDATION_REGISTRY,
    coalesceWindowMs: INVALIDATION_COALESCE_WINDOW_MS,
    onFrame: (type, data) => this.record(type, data),
  });

  /** Connection lifecycle for the header status, or `idle` before {@link start}. */
  get status(): Signal<SseStatus> {
    return this.spine.status;
  }

  /** `true` once the stream closed on a `401` — a session that expired
   * mid-stream; `false` before
   * {@link start} and for the whole life of a stream that never sees one. */
  get authFailed(): Signal<boolean> {
    return this.spine.authFailed;
  }

  /**
   * The recent-event feed for the Activity feed, oldest → newest, capped at
   * {@link LOG_LIMIT} and excluding the muted frames ({@link isLoggable}). Empty before
   * {@link start}.
   */
  get log(): Signal<readonly LoggedEvent[]> {
    return this._log.asReadonly();
  }

  /**
   * Open the live stream and wire it to the query cache. Idempotent — a second call
   * is a no-op. Auto-closes on the caller's {@link DestroyRef} (the app teardown).
   */
  start(): void {
    this.spine.start();
  }

  /** Append one frame to the bounded Activity feed ring, dropping the oldest past the cap.
   * Frames the feed mutes ({@link isLoggable}) never enter the ring — and never consume
   * a `seq`, so the panel's row keys stay dense. */
  private record(type: HubEventType, data: LoggedEventData): void {
    if (!isLoggable(type, data)) return;
    this._log.update((prev) => {
      // A question/decision notification and its chunk frame report the same
      // fact. Prefer the latter's fuller state regardless of arrival order;
      // replayed keyed frames also occupy just one slot in this bounded ring.
      const existing = data.key ? prev.findIndex((event) => event.key === data.key) : -1;
      if (existing >= 0) {
        if (prev[existing].type === HubEventType.CHUNK_CHANGED && type !== HubEventType.CHUNK_CHANGED) return prev;
        const replacement: LoggedEvent = { seq: prev[existing].seq, at: prev[existing].at, type, data, key: data.key ?? undefined };
        return [...prev.slice(0, existing), replacement, ...prev.slice(existing + 1)];
      }
      const entry: LoggedEvent = { seq: ++this.seq, type, data, at: Date.now(), key: data.key ?? undefined };
      const next = [...prev, entry];
      return next.length > LOG_LIMIT ? next.slice(next.length - LOG_LIMIT) : next;
    });
  }
}

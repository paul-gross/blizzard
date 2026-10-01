import { DestroyRef, EnvironmentInjector, Injectable, type Signal, inject, signal } from '@angular/core';
import { QueryClient } from '@tanstack/angular-query-experimental';

import {
  hubBacklogKey,
  hubChunkKey,
  hubChunksKey,
  hubEventsKey,
  hubFleetSpendKey,
  hubQuestionsKey,
  hubQueueKey,
  hubRunnersKey,
} from '../query-keys';
import { LiveInvalidationSpine } from './live-invalidation-spine';
import { type SseStatus, SseService } from './sse.service';

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

/** The named event types the hub broadcasts. */
export const HUB_EVENT_TYPES = [
  'chunk-changed',
  'question-asked',
  'question-answered',
  'decision-opened',
  'decision-resolved',
  'queue-changed',
  'runner-changed',
  'event-logged',
] as const;

/** A `chunk-changed` frame's payload. `chunk_id`/`status` are
 * always present; every other field is present-when-meaningful — omitted, never
 * `null`, when it does not apply (a chunk that has never transitioned carries no
 * `prev_node`, an unclaimed chunk carries no `runner_id`), which is why each is
 * declared optional rather than required-but-sometimes-absent. `by` rides
 * the `deleted` cause, mirroring {@link RunnerEvent.by}. */
export interface ChunkChanged {
  chunk_id: string;
  status: string;
  prev_status?: string;
  prev_node?: string;
  node?: string;
  runner_id?: string;
  cause?: string;
  graph_id?: string;
  by?: string;
}
/** A `question-asked`/`question-answered` frame's payload. */
export interface QuestionEvent {
  chunk_id: string;
  question_id: string;
}
/** A `decision-opened`/`decision-resolved` frame's payload. */
export interface DecisionEvent {
  chunk_id: string;
  decision_id: string;
}
/** A `runner-changed` frame's payload. `runner_id`/`kind` are always present — `kind`
 * names which registry change fired it, one of {@link RunnerChangeKind},
 * but typed `string` here because {@link HubEventPayload} intersects these shapes and
 * `event-logged` carries a `kind` of its own, from an unrelated vocabulary. `by` rides
 * the four pause/resume kinds and `reason` the runner-local pair, both omitted
 * otherwise. */
export interface RunnerEvent {
  runner_id: string;
  kind: string;
  by?: string;
  reason?: string;
}
/** An `event-logged` frame's payload — an operational event landed (`GET
 * /api/events`'s wire shape). `chunk_id` is always present, `null` rather than
 * omitted, for a runner-scoped event, and `runner_id` the same for a hub-authored one
 * (the broker's own shape) — unlike every other field on every other frame in this
 * module, both are required here, not optional, because the broker never omits either. */
export interface EventLoggedEvent {
  severity: string;
  kind: string;
  chunk_id: string | null;
  runner_id: string | null;
}
/** The fact-identity stamp the hub puts on every frame — the
 * merge/dedup key a backfilled row and the live frame reporting the same underlying
 * fact share. Omitted on any frame
 * with no durable fact behind it (`queue-changed`, a `registered`/`heartbeat`
 * `runner-changed`, an idempotent no-op) or from a hub that predates this stamp. */
export interface KeyedEvent {
  key?: string;
}
export type HubEventPayload = Partial<
  ChunkChanged & QuestionEvent & DecisionEvent & RunnerEvent & EventLoggedEvent & KeyedEvent
>;

/** One of the named event types the hub broadcasts ({@link HUB_EVENT_TYPES}). */
export type HubEventType = (typeof HUB_EVENT_TYPES)[number];

/** Which registry change a `runner-changed` frame reports (events/broker.py,
 * `RunnerChangeKind`). */
export type RunnerChangeKind =
  | 'registered'
  | 'heartbeat'
  | 'paused'
  | 'resumed'
  | 'locally-paused'
  | 'locally-resumed'
  | 'external-usage'
  | 'retired'
  | 'reinstated'
  | 'token-revoked';

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
const NO_DURABLE_FACT_TYPES: ReadonlySet<string> = new Set<HubEventType>(['queue-changed']);

/**
 * The `runner-changed` kinds the Activity feed drops — {@link
 * NO_DURABLE_FACT_TYPES}'s rule applied within this one type. A runner re-registers on
 * every pull-loop cycle as its liveness heartbeat, so these two are the overwhelming
 * majority of all frames and carry no news an operator can act on — left in, they would
 * evict every other event out of the {@link LOG_LIMIT} ring within a few cycles, so this
 * is what keeps the feed legible rather than merely tidier. Dropping is scoped to the
 * feed: {@link LiveInvalidationSpine.dispatch} still invalidates on them, so the fleet
 * registry's liveness column keeps refreshing on every heartbeat exactly as before.
 *
 * `external-usage` is muted for a different reason: it is not an
 * operator-visible activity-feed entry, and carries no `key` — there is no fact-table row
 * identity worth naming, only an advisory display field the fleet registry re-reads.
 */
const MUTED_RUNNER_KINDS: ReadonlySet<string> = new Set<RunnerChangeKind>([
  'registered',
  'heartbeat',
  'external-usage',
]);

/** Causes represented by the chunk fact sources in `activity_facts_since`. A
 * chunk-scoped telemetry fact can repeat the last transition's status and key;
 * neither makes that fact another activity occurrence. */
const ACTIVITY_CHUNK_CAUSES: ReadonlySet<string> = new Set([
  'minted', 'promoted', 'grouped', 'claimed', 'node-completed', 'hub-advanced',
  'migrated', 'restarted', 'decision-submitted', 'decision-resolved',
  'question-asked', 'question-answered', 'escalated', 'requeued', 'detached',
  'paused', 'resumed', 'stopped', 'completed', 'deleted',
]);

/** Whether a frame belongs in the Activity feed — see {@link NO_DURABLE_FACT_TYPES} and
 * {@link MUTED_RUNNER_KINDS}. */
function isLoggable(type: string, data: HubEventPayload): boolean {
  if (NO_DURABLE_FACT_TYPES.has(type)) return false;
  if (type === 'chunk-changed') return ACTIVITY_CHUNK_CAUSES.has(data.cause ?? '');
  return type !== 'runner-changed' || !MUTED_RUNNER_KINDS.has(data.kind ?? '');
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
    ...(data.chunk_id ? [hubChunkKey(data.chunk_id)] : []),
    hubFleetSpendKey,
    hubEventsKey,
  ];
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
 * The event → query-key invalidation registry — the single place a live
 * event names what it stales, so wiring a new live feature into the SSE spine is
 * adding a row here, not a `case` in {@link LiveInvalidationSpine.dispatch}. Exhaustive
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
  // A backlog reorder fires the same event as a ready-queue one (no distinct
  // payload key, `bzh:ranking-is-per-list`), so both cached orders invalidate.
  'queue-changed': () => [hubQueueKey, hubBacklogKey],
  'runner-changed': () => [hubRunnersKey],
  'event-logged': (data) => [hubEventsKey, ...(data.chunk_id ? [hubChunkKey(data.chunk_id)] : [])],
};

/**
 * One event recorded for the Activity feed: its stream arrival order
 * (`seq` — a stable, monotonic client key), its board vocabulary `type`, the parsed
 * `data`, and the client-side arrival time `at` (ms epoch; the hub frames carry no
 * timestamp of their own).
 *
 * `key` is `data.key` ({@link KeyedEvent}), lifted to the top level. Absent on a frame from a hub that predates this stamp.
 */
export interface LoggedEvent {
  readonly seq: number;
  readonly type: string;
  readonly data: HubEventPayload;
  readonly at: number;
  readonly key?: string;
}

/**
 * Recent-event ring cap for *this live tee alone* — matches the broker's history depth
 * (events/broker.py, `history=256`) so the ring never holds more than a fresh connect's
 * own replay tail could ever deliver. This is no longer the
 * whole story for what the Activity feed panel renders: its container additionally
 * backfills on load from `GET /api/activity`, a separate, durable-store-backed source
 * this ring knows nothing about (`activity-panel.ts`'s `RENDER_LIMIT`, reconciled with
 * that read's own `limit` rather than derived from this one).
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
   * mid-stream. The app root watches this and routes to `/login`; `false` before
   * {@link start} and for the whole life of a stream that never sees one. */
  get authFailed(): Signal<boolean> {
    return this.spine.authFailed;
  }

  /**
   * The recent-event feed for the Activity feed, oldest → newest, capped at
   * {@link LOG_LIMIT} and excluding the muted frames ({@link isLoggable}). Empty before
   * {@link start}; the panel reverses it for display.
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
  private record(type: string, data: HubEventPayload): void {
    if (!isLoggable(type, data)) return;
    this._log.update((prev) => {
      // A question/decision notification and its chunk frame report the same
      // fact. Prefer the latter's fuller state regardless of arrival order;
      // replayed keyed frames also occupy just one slot in this bounded ring.
      const existing = data.key ? prev.findIndex((event) => event.key === data.key) : -1;
      if (existing >= 0) {
        if (prev[existing].type === 'chunk-changed' && type !== 'chunk-changed') return prev;
        const replacement: LoggedEvent = { seq: prev[existing].seq, at: prev[existing].at, type, data, key: data.key };
        return [...prev.slice(0, existing), replacement, ...prev.slice(existing + 1)];
      }
      const entry: LoggedEvent = { seq: ++this.seq, type, data, at: Date.now(), key: data.key };
      const next = [...prev, entry];
      return next.length > LOG_LIMIT ? next.slice(next.length - LOG_LIMIT) : next;
    });
  }
}

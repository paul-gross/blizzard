import { DestroyRef, EnvironmentInjector, Injectable, type Signal, effect, inject, signal, untracked } from '@angular/core';
import { QueryClient } from '@tanstack/angular-query-experimental';

import {
  hubChunkKey,
  hubChunksKey,
  hubEventsKey,
  hubFleetSpendKey,
  hubQuestionsKey,
  hubQueueKey,
  hubRunnersKey,
} from '../query-keys';
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
  'event-logged',
] as const;

/** A `chunk-changed` frame's payload (issue #212). `chunk_id`/`status` are always
 * present; every other field is present-when-meaningful — omitted, never `null`, when
 * it does not apply (a chunk that has never transitioned carries no `prev_node`, an
 * unclaimed chunk carries no `runner_id`). `graph_id` rides the wire but is never
 * rendered — the Event log's block row stops at the transition and runner lines. */
interface ChunkChanged {
  chunk_id: string;
  status: string;
  prev_status: string;
  prev_node: string;
  node: string;
  runner_id: string;
  cause: string;
  graph_id: string;
}
interface QuestionEvent {
  chunk_id: string;
  question_id: string;
}
interface DecisionEvent {
  chunk_id: string;
  decision_id: string;
}
/** A `runner-changed` frame's payload. `kind` names which registry change fired it
 * (issue #151) — one of {@link RunnerChangeKind}, but typed `string` here because
 * {@link HubEventPayload} intersects these shapes and `event-logged` carries a `kind` of
 * its own, from an unrelated vocabulary. `by` rides the four pause/resume kinds and
 * `reason` the runner-local pair, both absent otherwise; a frame from a hub older than
 * #151 carries no `kind` at all. */
interface RunnerEvent {
  runner_id: string;
  kind: string;
  by: string;
  reason: string;
}
/** An `event-logged` frame's payload — an operational event landed (`GET
 * /api/events`'s wire shape, Phase 4). `chunk_id` is `null`, not absent, for a
 * runner-scoped event (the broker's own shape), unlike the other frames' payloads. */
interface EventLoggedEvent {
  severity: string;
  kind: string;
  chunk_id: string | null;
  runner_id: string;
}
/** The fact-identity stamp the hub puts on every frame (issue #213 Phase 2) — the
 * merge/dedup key a backfilled row and the live frame reporting the same underlying
 * fact share, so a consumer that reads both (the Event log's backfill, Phase 4) can
 * tell they are the same event rather than rendering it twice. Absent on a frame from
 * a hub older than Phase 2. */
interface KeyedEvent {
  key: string;
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
  | 'external-usage';

/**
 * The `runner-changed` kinds the Event log feed drops (issue #151). A runner re-registers
 * on every pull-loop cycle as its liveness heartbeat, so these two are the overwhelming
 * majority of all frames and carry no news an operator can act on — left in, they would
 * evict every other event out of the {@link LOG_LIMIT} ring within a few cycles, so this
 * is what keeps the feed legible rather than merely tidier. Dropping is scoped to the
 * feed: {@link FleetLiveUpdates.dispatch} still invalidates on them, so the fleet
 * registry's liveness column keeps refreshing on every heartbeat exactly as before.
 *
 * `external-usage` (issue #218) is muted for a different reason: it is not an
 * operator-visible event-log entry, and carries no `key` — there is no fact-table row
 * identity worth naming, only an advisory display field the fleet registry re-reads.
 */
const MUTED_RUNNER_KINDS: ReadonlySet<string> = new Set<RunnerChangeKind>([
  'registered',
  'heartbeat',
  'external-usage',
]);

/** Whether a frame belongs in the Event log feed — see {@link MUTED_RUNNER_KINDS}. */
function isLoggable(type: string, data: HubEventPayload): boolean {
  return type !== 'runner-changed' || !MUTED_RUNNER_KINDS.has(data.kind ?? '');
}

/** A chunk-changed frame invalidates the fleet list, the ready queue (a status flip
 * can add or remove a chunk from it), that chunk's own detail when the payload names
 * one, and the fleet spend-since read: usage rides the same fact a chunk-changed
 * reports (issue #60), so a chunk's derived cost total and the fleet-wide spend both
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
  'event-logged': (data) => [hubEventsKey, ...(data.chunk_id ? [hubChunkKey(data.chunk_id)] : [])],
};

/**
 * One event recorded for the Event log feed (issue #25): its stream arrival order
 * (`seq` — a stable, monotonic client key), its board vocabulary `type`, the parsed
 * `data`, and the client-side arrival time `at` (ms epoch; the hub frames carry no
 * timestamp of their own). Presentation — the human-readable summary — is the panel's.
 *
 * `key` rides `data.key` (issue #213 Phase 4) so the panel's backfill/live merge can
 * dedupe a backfilled row against the live frame reporting the same fact without
 * reaching back into `data` itself. Absent on a frame from a hub older than Phase 2.
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
 * own replay tail could ever deliver. Since issue #213 Phase 4 this is no longer the
 * whole story for what the Event log panel renders: its container additionally
 * backfills on load from `GET /api/activity`, a separate, durable-store-backed source
 * this ring knows nothing about (`event-log-panel.ts`'s `RENDER_LIMIT`, reconciled with
 * that read's own `limit` rather than derived from this one).
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
 * renders (issue #25): because the same single subscription records each frame, the
 * broker's connect-time replay (its buffered history) lands in the log as backfill for
 * free, and the query-invalidation dispatch stays exactly as it was. The tee is where
 * the feed's noise floor is set — {@link isLoggable} mutes frames an operator cannot act
 * on, and only there, never on the dispatch side.
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
    // `untracked` around the `effect()` call itself (not its body): `start()` may be
    // invoked from within another reactive context (the app root's `afterRenderEffect`
    // wiring, which tracks signals same as `effect`), and `effect()` asserts it is not
    // called from one (NG0602) — `untracked` clears the active consumer for the
    // duration of this call, so `start()` stays safe regardless of its caller's context.
    let lastReopens = handle.reopens();
    const ref = untracked(() =>
      effect(
        () => {
          const reopens = handle.reopens();
          if (reopens > lastReopens) {
            lastReopens = reopens;
            void this.queryClient.invalidateQueries();
          }
        },
        { injector: this.injector },
      ),
    );

    this.destroyRef.onDestroy(() => {
      sub.unsubscribe();
      ref.destroy();
      handle.close();
      this.handle = null;
    });
  }

  /** Append one frame to the bounded Event log ring, dropping the oldest past the cap.
   * Frames the feed mutes ({@link isLoggable}) never enter the ring — and never consume
   * a `seq`, so the panel's row keys stay dense. */
  private record(type: string, data: HubEventPayload): void {
    if (!isLoggable(type, data)) return;
    const entry: LoggedEvent = { seq: ++this.seq, type, data, at: Date.now(), key: data.key };
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

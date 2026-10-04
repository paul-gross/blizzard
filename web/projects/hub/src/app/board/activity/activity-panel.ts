import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { type ActivityView, compactRef, type KitAsyncStateValue, asyncState, FleetLiveUpdates, hubApi, type LoggedEvent, type RunnerChangeKind, formatClockTime } from 'fleet';
import { ACTIVITY_LIMIT, injectHubActivityQuery } from './activity.query';
import { ActivityFeedView, type ActivityRow } from './activity-view';
import { summarizeChunkChange } from './chunk-change-summary';

const { HubEventType, RunnerChangeKind: Kind } = hubApi;

/** The verb a `runner-changed` kind reads as, where the kind alone does not already read
 * as one. Only the pause and retirement families need an entry: the registration and heartbeat kinds
 * never reach the feed (`FleetLiveUpdates` mutes them), and the fallback below
 * renders any kind absent here — including one from a newer hub — as itself. */
const RUNNER_CHANGE_VERB: ReadonlyMap<string, string> = new Map<RunnerChangeKind, string>([
  [Kind.PAUSED, 'paused'],
  [Kind.RESUMED, 'resumed'],
  [Kind.LOCALLY_PAUSED, 'locally paused'],
  [Kind.LOCALLY_RESUMED, 'locally resumed'],
  [Kind.RETIRED, 'retired'],
  [Kind.REINSTATED, 'reinstated'],
  [Kind.TOKEN_REVOKED, 'had its token revoked'],
]);

/**
 * A `runner-changed` frame as prose — e.g. `runner runner-local paused by
 * operator`, or `runner runner-local locally paused by runner-ceiling — spend ceiling
 * reached`. A kind with no phrasing above renders as the raw kind — pinned by
 * `activity-panel.spec.ts`'s "renders a runner-changed frame of an unrecognized kind as
 * its raw kind, keeping the row".
 */
function summarizeRunnerChange(data: LoggedEvent['data']): string {
  const runner = `runner ${compactRef(data.runner_id ?? '—')}`;
  const verb = data.kind ? (RUNNER_CHANGE_VERB.get(data.kind) ?? data.kind) : 'changed';
  const by = data.by ? ` by ${data.by}` : '';
  const reason = data.reason ? ` — ${data.reason}` : '';
  return `${runner} ${verb}${by}${reason}`;
}

/** A rendered row's message (line 1) and optional detail (line 2, `chunk-changed`
 * only — see {@link summarizeChunkChange}). */
interface RowSummary {
  readonly message: string;
  readonly detail?: string;
}

/**
 * A human-readable summary of a hub event ("a legible summary"; widened to a two-line
 * block for `chunk-changed`). Maps the generated `HubEventType` vocabulary onto plain
 * phrasing; an unknown type degrades to its raw name rather than dropping the row.
 */
function summarize(event: LoggedEvent): RowSummary {
  const chunk = event.data.chunk_id ? compactRef(event.data.chunk_id) : '';
  switch (event.type) {
    case HubEventType.CHUNK_CHANGED: {
      const { transition, runner } = summarizeChunkChange(event.data);
      return { message: transition, detail: runner };
    }
    case HubEventType.QUESTION_ASKED:
      return { message: `${chunk} asked a question` };
    case HubEventType.QUESTION_ANSWERED:
      return { message: `${chunk} question answered` };
    case HubEventType.DECISION_OPENED:
      return { message: `${chunk} gate opened` };
    case HubEventType.DECISION_RESOLVED:
      return { message: `${chunk} gate resolved` };
    case HubEventType.QUEUE_CHANGED:
      return { message: 'ready queue changed' };
    case HubEventType.RUNNER_CHANGED:
      return { message: summarizeRunnerChange(event.data) };
    case HubEventType.EVENT_LOGGED:
      return {
        message: `${chunk || compactRef(event.data.runner_id ?? '—')} · ${event.data.severity ?? '—'} ${event.data.kind ?? '—'}`,
      };
    default:
      return { message: event.type };
  }
}

/** The rendered-row cap for the merged backfill + live feed — the backfill read's own limit. */
const RENDER_LIMIT = ACTIVITY_LIMIT;

/** Shape one `GET /api/activity` row into the same {@link LoggedEvent} shape the live
 * SSE tee produces, so {@link summarize} (and {@link summarizeChunkChange}) run
 * unchanged over either source. `seq` is caller-assigned (negative, so it can never
 * collide with the live spine's own positive, monotonic counter) — it exists only so
 * the view has a stable `track` key, not for ordering (that's `at`). `at` is parsed
 * from the wire's ISO instant into the ms epoch {@link LoggedEvent.at} expects. The
 * row less its envelope is already {@link LoggedEvent.data}'s shape. */
function fromActivity(row: ActivityView, seq: number): LoggedEvent {
  const { at, type, ...data } = row;
  return { seq, type, data, at: Date.parse(at), key: row.key };
}

/**
 * The Activity feed panel's **container** (`bzh:frontend-container-presentational`).
 *
 * Owns two independent reads of the same underlying feed and merges them into one
 * rendered list:
 *
 * - The **live** tee: {@link FleetLiveUpdates}'s bounded SSE ring, the bridge from
 *   transport to query cache and the destination for the broker's connect-time replay.
 * - The **backfill**: {@link injectHubActivityQuery}, a one-shot `GET /api/activity`
 *   read on mount, so the feed shows recent history immediately rather than starting
 *   empty and filling in only as new frames arrive.
 *
 * The two are merged in {@link merged}: a backfilled row and a live frame naming the
 * same `key` must render as exactly one row, preferring the live
 * copy (it may carry more current info) — so the merge drops a backfilled row whose
 * `key` also names a live frame already present, never the other way around. A row
 * with no `key` at all can't collide with anything and always renders standalone. The
 * merged list is newest-first-capped at {@link RENDER_LIMIT} by sorting on `at`, not by
 * trusting either source's own ordering (the backfill arrives newest-first over the
 * wire; the live ring is oldest-first) — sorting once here is one rule instead of two
 * assumptions to keep in sync.
 */
@Component({
  selector: 'app-activity-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ActivityFeedView],
  templateUrl: './activity-panel.html',
})
export class ActivityPanel {
  private readonly live = inject(FleetLiveUpdates);
  protected readonly activityQuery = injectHubActivityQuery();

  /** The backfill read shaped into {@link LoggedEvent}s, oldest-assignment-order
   * irrelevant (sorted away in {@link merged}). Empty until the first read resolves. */
  private readonly backfill = computed<readonly LoggedEvent[]>(() =>
    (this.activityQuery.data() ?? []).map((row, i) => fromActivity(row, -1 - i)),
  );

  /** The backfill and live feeds merged and deduped by `key` (see the class doc),
   * oldest → newest, capped at {@link RENDER_LIMIT} — the same shape
   * {@link FleetLiveUpdates.log} produces on its own, so {@link rows} below needs no
   * branch on which source a given entry came from. */
  private readonly merged = computed<readonly LoggedEvent[]>(() => {
    const live = this.live.log();
    const liveKeys = new Set(live.flatMap((event) => (event.key ? [event.key] : [])));
    const backfillOnly = this.backfill().filter((event) => !event.key || !liveKeys.has(event.key));
    const combined = [...backfillOnly, ...live].sort((a, b) => a.at - b.at);
    return combined.length > RENDER_LIMIT ? combined.slice(combined.length - RENDER_LIMIT) : combined;
  });

  /** The merged feed newest-first, each frame shaped into its display row. */
  protected readonly rows = computed<readonly ActivityRow[]>(() =>
    this.merged()
      .map((event) => ({
        seq: event.seq,
        type: event.type,
        time: formatClockTime(event.at),
        ...summarize(event),
      }))
      .reverse(),
  );

  /**
   * The panel's async state: the backfill query drives loading/error/empty/ready
   * ({@link asyncState}) — a first in-flight fetch renders `'loading'`, not `'empty'` —
   * with one override: a hard SSE auth failure (`authFailed`, the stream closed on a
   * `401` with no reconnect scheduled) always reads as `'error'`, since that's a real
   * degraded state the backfill query alone never observes (it only ever runs once).
   * Auth failure wins if both are somehow true.
   */
  protected readonly state = computed<KitAsyncStateValue>(() => {
    if (this.live.status() === 'closed' && this.live.authFailed()) return 'error';
    return asyncState(this.activityQuery, this.rows().length === 0);
  });
}

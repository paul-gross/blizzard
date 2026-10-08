import { type ActivityView, asyncState, type AsyncStateQuery, compactRef, formatClockTime, hubApi, type KitAsyncStateValue, type LoggedEvent, type RunnerChangeKind, runnerDisplayName } from 'fleet';
import { type ActivityRow } from './activity-view';
import { summarizeChunkChange } from './chunk-change-summary';

const { ActivityChunkChangeCause, HubEventType, RunnerChangeKind: Kind } = hubApi;

/** The verb a `runner-changed` kind reads as, where the kind alone does not already read
 * as one; {@link summarizeRunnerChange} renders any kind absent here as itself. */
const RUNNER_CHANGE_VERB: ReadonlyMap<string, string> = new Map<RunnerChangeKind, string>([
  [Kind.ADDED, 'added to the fleet'],
  [Kind.PAUSED, 'paused'],
  [Kind.RESUMED, 'resumed'],
  [Kind.LOCALLY_PAUSED, 'locally paused'],
  [Kind.LOCALLY_RESUMED, 'locally resumed'],
  [Kind.RETIRED, 'retired'],
  [Kind.REINSTATED, 'reinstated'],
  [Kind.TOKEN_REVOKED, 'had its token revoked'],
]);

/**
 * A `runner-changed` frame as prose, naming the runner by display name — e.g.
 * `runner R-ABF3.r-claude paused by operator`, or `runner R-ABF3.r-claude locally paused by runner-ceiling — spend ceiling
 * reached`. A kind with no phrasing above renders as the raw kind — pinned by
 * `activity-panel.spec.ts`'s "renders a runner-changed frame of an unrecognized kind as
 * its raw kind, keeping the row".
 */
function summarizeRunnerChange(data: LoggedEvent['data']): string {
  const runner = `runner ${data.runner_id ? runnerDisplayName(data.runner_id, data.runner_name) : '—'}`;
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
 * A human-readable summary of a hub event, two lines for `chunk-changed`. Maps the
 * generated `HubEventType` vocabulary onto plain phrasing; an unknown type degrades to
 * its raw name rather than dropping the row.
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
        message: `${chunk || (event.data.runner_id ? runnerDisplayName(event.data.runner_id, event.data.runner_name) : '—')} · ${event.data.severity ?? '—'} ${event.data.kind ?? '—'}`,
      };
    default:
      return { message: event.type };
  }
}

/** Shape one activity row into a {@link LoggedEvent}, so {@link summarize} runs unchanged
 * over backfilled and live frames. `seq` is caller-assigned and serves only as a stable
 * `track` key, never for ordering (that's `at`). */
function fromActivity(row: ActivityView, seq: number): LoggedEvent {
  const { at, type, ...data } = row;
  return { seq, type, data, at: Date.parse(at), key: row.key };
}

/** The backfill read shaped into {@link LoggedEvent}s, each given a distinct negative `seq`
 * (see {@link fromActivity}). Empty until the first read resolves. */
export function backfillEvents(rows: readonly ActivityView[] | undefined): readonly LoggedEvent[] {
  return (rows ?? []).map((row, i) => fromActivity(row, -1 - i));
}

/** Whether a frame reports its chunk deleted. */
function isChunkDeletion(event: LoggedEvent): boolean {
  return event.type === HubEventType.CHUNK_CHANGED && event.data.cause === ActivityChunkChangeCause.DELETED;
}

/** The backfill and live feeds merged and deduped by `key` — a backfilled row whose `key`
 * also names a live frame is dropped, a keyless row always stays — with a deleted chunk's
 * facts suppressed per `blizzard-context:/domain/operations.md` §The activity feed, sorted
 * oldest → newest on `at` and capped to the newest `limit`. */
export function mergeActivityFeeds(
  backfill: readonly LoggedEvent[],
  live: readonly LoggedEvent[],
  limit: number,
): readonly LoggedEvent[] {
  const liveKeys = new Set(live.flatMap((event) => (event.key ? [event.key] : [])));
  const backfillOnly = backfill.filter((event) => !event.key || !liveKeys.has(event.key));
  // A replayed live frame is stamped with its receipt time; the backfill row for the same
  // fact carries the event's real time, so that time wins on a key collision.
  const backfillTimes = new Map(backfill.flatMap((event) => (event.key ? [[event.key, event.at] as const] : [])));
  const liveTimed = live.map((event) => {
    const at = event.key ? backfillTimes.get(event.key) : undefined;
    return at === undefined ? event : { ...event, at };
  });
  const deleted = new Set([...backfill, ...live].flatMap((event) => (isChunkDeletion(event) && event.data.chunk_id ? [event.data.chunk_id] : [])));
  const combined = [...backfillOnly, ...liveTimed]
    .filter((event) => !event.data.chunk_id || !deleted.has(event.data.chunk_id) || isChunkDeletion(event))
    .sort((a, b) => a.at - b.at);
  return combined.length > limit ? combined.slice(combined.length - limit) : combined;
}

/** The merged feed newest-first, each frame shaped into its display row. */
export function activityRows(merged: readonly LoggedEvent[]): readonly ActivityRow[] {
  return merged
    .map((event) => ({
      seq: event.seq,
      type: event.type,
      time: formatClockTime(event.at),
      ...summarize(event),
    }))
    .reverse();
}

/** The panel's async state: a hard SSE auth failure reads as `'error'` whatever the
 * backfill holds; otherwise the backfill query's own triad ({@link asyncState}). */
export function activityPanelState(authFailed: boolean, query: AsyncStateQuery, isEmpty: boolean): KitAsyncStateValue {
  if (authFailed) return 'error';
  return asyncState(query, isEmpty);
}

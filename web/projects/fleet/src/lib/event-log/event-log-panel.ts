import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { type ActivityView } from '../api/hub';
import { compactRef } from '../compact-ref';
import type { KitAsyncStateValue } from '../kit/kit-async-state';
import { asyncState } from '../query-state';
import { FleetLiveUpdates, type HubEventPayload, type LoggedEvent, type RunnerChangeKind } from '../sse/fleet-live';
import { formatClockTime } from '../when';
import { injectHubActivityQuery } from './activity.query';
import { summarizeChunkChange } from './chunk-change-summary';
import { EventLogView, type LogRow } from './event-log-view';

/** The verb a `runner-changed` kind reads as, where the kind alone does not already read
 * as one. Only the pause family needs an entry: the registration and heartbeat kinds
 * never reach the feed (fleet-live.ts, `MUTED_RUNNER_KINDS`), and the fallback below
 * renders any kind absent here as itself. */
const RUNNER_CHANGE_VERB: ReadonlyMap<string, string> = new Map<RunnerChangeKind, string>([
  ['paused', 'paused'],
  ['resumed', 'resumed'],
  ['locally-paused', 'locally paused'],
  ['locally-resumed', 'locally resumed'],
]);

/**
 * A `runner-changed` frame as prose (issue #151) — e.g. `runner runner-local paused by
 * operator`, or `runner runner-local locally paused by runner-ceiling — spend ceiling
 * reached`. A kind with no phrasing above degrades to the raw kind rather than dropping
 * the row, on the same reasoning as {@link summarize}'s default: an unrecognized frame is
 * news that this board is older than the hub, and silence would hide it.
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
 * A human-readable summary of a hub event (issue #25 — "a legible summary"; widened by
 * issue #212 to a two-line block for `chunk-changed`). Maps the board's live vocabulary
 * (events/broker.py) onto plain phrasing; an unknown type degrades to its raw name
 * rather than dropping the row.
 */
function summarize(event: LoggedEvent): RowSummary {
  const chunk = event.data.chunk_id ? compactRef(event.data.chunk_id) : '';
  switch (event.type) {
    case 'chunk-changed': {
      const { transition, runner } = summarizeChunkChange(event.data);
      return { message: transition, detail: runner };
    }
    case 'question-asked':
      return { message: `${chunk} asked a question` };
    case 'question-answered':
      return { message: `${chunk} question answered` };
    case 'decision-opened':
      return { message: `${chunk} gate opened` };
    case 'decision-resolved':
      return { message: `${chunk} gate resolved` };
    case 'queue-changed':
      return { message: 'ready queue changed' };
    case 'runner-changed':
      return { message: summarizeRunnerChange(event.data) };
    case 'event-logged':
      return {
        message: `${chunk || compactRef(event.data.runner_id ?? '—')} · ${event.data.severity ?? '—'} ${event.data.kind ?? '—'}`,
      };
    default:
      return { message: event.type };
  }
}

/**
 * The rendered-row cap for the merged backfill + live feed (issue #213 Phase 4) —
 * reconciled with the backend's own `GET /api/activity` `limit` (`ACTIVITY_LIMIT`,
 * `activity.query.ts`) so the two stay the same number in one place a future reader can
 * find, rather than two caps that happen to agree by coincidence.
 */
const RENDER_LIMIT = 200;

/** Shape one `GET /api/activity` row into the same {@link LoggedEvent} shape the live
 * SSE tee produces, so {@link summarize} (and {@link summarizeChunkChange}) run
 * unchanged over either source. `seq` is caller-assigned (negative, so it can never
 * collide with the live spine's own positive, monotonic counter) — it exists only so
 * the view has a stable `track` key, not for ordering (that's `at`). `at` is parsed
 * from the wire's ISO instant into the ms epoch {@link LoggedEvent.at} expects.
 *
 * Field-by-field rather than a blind spread: `ActivityView`'s optional fields are
 * `T | null | undefined` (an explicit "absent" from a JSON API), while
 * `HubEventPayload`'s are `T | undefined` (`Partial`) — the seam every present-when-
 * meaningful field needs `?? undefined` to cross. */
function fromActivity(row: ActivityView, seq: number): LoggedEvent {
  const data: HubEventPayload = {
    chunk_id: row.chunk_id ?? undefined,
    status: row.status ?? undefined,
    prev_status: row.prev_status ?? undefined,
    prev_node: row.prev_node ?? undefined,
    node: row.node ?? undefined,
    runner_id: row.runner_id ?? undefined,
    cause: row.cause ?? undefined,
    graph_id: row.graph_id ?? undefined,
    kind: row.kind ?? undefined,
    by: row.by ?? undefined,
    reason: row.reason ?? undefined,
    severity: row.severity ?? undefined,
    key: row.key,
  };
  return { seq, type: row.type, data, at: Date.parse(row.at), key: row.key };
}

/**
 * The Event log panel's **container** (issue #213 Phase 4, split from the formerly
 * presentational `event-log-panel.ts` — `bzh:frontend-container-presentational`).
 *
 * Owns two independent reads of the same underlying feed and merges them into one
 * rendered list:
 *
 * - The **live** tee: {@link FleetLiveUpdates}'s bounded SSE ring, unchanged from
 *   before this phase — still the sanctioned bridge from the transport to the query
 *   cache, and still the source the broker's connect-time replay lands in for free.
 * - The **backfill**: {@link injectHubActivityQuery}, a one-shot `GET /api/activity`
 *   read on mount, so the feed shows recent history immediately rather than starting
 *   empty and filling in only as new frames arrive.
 *
 * The two are merged in {@link merged}: a backfilled row and a live frame naming the
 * same `key` (issue #213 Phase 2) must render as exactly one row, preferring the live
 * copy (it may carry more current info) — so the merge drops a backfilled row whose
 * `key` also names a live frame already present, never the other way around. A row
 * with no `key` at all can't collide with anything and always renders standalone. The
 * merged list is newest-first-capped at {@link RENDER_LIMIT} by sorting on `at`, not by
 * trusting either source's own ordering (the backfill arrives newest-first over the
 * wire; the live ring is oldest-first) — sorting once here is one rule instead of two
 * assumptions to keep in sync.
 */
@Component({
  selector: 'fleet-event-log-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EventLogView],
  template: `<fleet-event-log-view [rows]="rows()" [state]="state()" />`,
})
export class EventLogPanel {
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
  protected readonly rows = computed<readonly LogRow[]>(() =>
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

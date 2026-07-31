import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { compactRef } from '../compact-ref';
import { summarizeChunkChange } from './chunk-change-summary';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitPanel } from '../kit/kit-panel';
import { FleetLiveUpdates, type LoggedEvent, type RunnerChangeKind } from '../sse/fleet-live';
import { formatClockTime } from '../when';

/** One rendered Event log row — the logged frame plus its display strings.
 * `detail` is the block row's second line (`chunk-changed` only, issue #212); every
 * other event type leaves it unset and renders as the single-line row it always has. */
interface LogRow {
  readonly seq: number;
  readonly type: string;
  readonly time: string;
  readonly message: string;
  readonly detail?: string;
}

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
 * The Event log panel (issue #25) — a scrolling, newest-first feed of recent fleet
 * events with a running count.
 *
 * Presentational: it holds no transport and opens no stream of its own. It reads the
 * bounded feed {@link FleetLiveUpdates} already tees off the board's single SSE
 * subscription, so the broker's connect-time replay arrives as backfill for free and
 * the existing query-invalidation behavior is untouched. All color comes from the
 * design-token layer (design/tokens.css), never hard-coded hex.
 */
@Component({
  selector: 'fleet-event-log-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel],
  template: `
    <fleet-kit-panel
      class="fill"
      aria-label="Event log"
      data-testid="event-log-panel"
      label="Event log"
    >
      <fleet-kit-async-state
        [state]="state()"
        placement="inline"
        loadingText="CONNECTING…"
        loadingTestid="event-log-loading"
        errorText="EVENT STREAM UNAVAILABLE"
        errorTestid="event-log-error"
        emptyText="No events yet."
        emptyTestid="event-log-empty"
      >
        <div class="rows" data-testid="event-log-rows">
          @for (row of rows(); track row.seq) {
            <div class="ev" data-testid="event-log-row" [attr.data-kind]="row.type">
              <span class="t" data-testid="event-log-time">{{ row.time }}</span>
              <div class="m-block">
                <span class="m" data-testid="event-log-message">{{ row.message }}</span>
                @if (row.detail) {
                  <span class="d" data-testid="event-log-detail">{{ row.detail }}</span>
                }
              </div>
            </div>
          }
        </div>
      </fleet-kit-async-state>
    </fleet-kit-panel>
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      min-height: 0;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
      color: var(--text);
    }
    fleet-kit-panel.fill {
      flex: 1;
    }
    .rows {
      overflow-y: auto;
      min-height: 0;
      flex: 1;
    }
    .ev {
      display: grid;
      grid-template-columns: 64px 1fr;
      gap: 14px;
      padding: 2px 8px;
      border-bottom: 1px solid var(--line);
      font-size: var(--fs-sm);
      line-height: 1.5;
    }
    .ev .t {
      color: var(--label-dim);
    }
    .m-block {
      display: flex;
      flex-direction: column;
    }
    .ev .m {
      color: var(--text);
      overflow-wrap: anywhere;
    }
    .ev .d {
      color: var(--label-dim);
      overflow-wrap: anywhere;
    }
  `,
})
export class EventLogPanel {
  private readonly live = inject(FleetLiveUpdates);

  /** The feed newest-first, each frame shaped into its display row. */
  protected readonly rows = computed<readonly LogRow[]>(() =>
    this.live
      .log()
      .map((event) => ({
        seq: event.seq,
        type: event.type,
        time: formatClockTime(event.at),
        ...summarize(event),
      }))
      .reverse(),
  );

  /**
   * This panel has no query — its read is a client-side ring fed by
   * {@link FleetLiveUpdates}'s SSE handle, so its own connection status stands
   * in for a query's pending/error (AC 3). `'idle'` is the one status that
   * means "never yet connected" (`SseService.connect`'s initial value, before
   * the stream's first `onopen`), so it alone maps to `'loading'` — a
   * `'reconnecting'` blip after data has already arrived must not regress an
   * already-rendered feed back to a loading state (the same AC 6 guarantee
   * {@link asyncState} gives a query-backed read). A hard auth failure
   * (`authFailed`, `'closed'` with no reconnect scheduled) is the one case
   * that reads as an error rather than an empty, still-live feed.
   */
  protected readonly state = computed<KitAsyncStateValue>(() => {
    if (this.live.status() === 'idle') return 'loading';
    if (this.live.status() === 'closed' && this.live.authFailed()) return 'error';
    return this.rows().length === 0 ? 'empty' : 'ready';
  });
}

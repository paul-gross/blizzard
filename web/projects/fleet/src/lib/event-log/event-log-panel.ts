import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { compactRef } from '../compact-ref';
import { KitPanel } from '../kit/kit-panel';
import { FleetLiveUpdates, type LoggedEvent, type RunnerChangeKind } from '../sse/fleet-live';
import { formatClockTime } from '../when';

/** One rendered Event log row — the logged frame plus its display strings. */
interface LogRow {
  readonly seq: number;
  readonly type: string;
  readonly time: string;
  readonly message: string;
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

/**
 * A human-readable one-line summary of a hub event (issue #25 — "a legible summary").
 * Maps the board's live vocabulary (events/broker.py) onto plain phrasing; an unknown
 * type degrades to its raw name rather than dropping the row.
 */
function summarize(event: LoggedEvent): string {
  const chunk = event.data.chunk_id ? compactRef(event.data.chunk_id) : '';
  switch (event.type) {
    case 'chunk-changed':
      return `${chunk} → ${event.data.status ?? '—'}`;
    case 'question-asked':
      return `${chunk} asked a question`;
    case 'question-answered':
      return `${chunk} question answered`;
    case 'answer-delivered':
      return `${chunk} answer delivered`;
    case 'decision-opened':
      return `${chunk} gate opened`;
    case 'decision-resolved':
      return `${chunk} gate resolved`;
    case 'queue-changed':
      return 'ready queue changed';
    case 'runner-changed':
      return summarizeRunnerChange(event.data);
    case 'event-logged':
      return `${chunk || compactRef(event.data.runner_id ?? '—')} · ${event.data.severity ?? '—'} ${event.data.kind ?? '—'}`;
    default:
      return event.type;
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
  imports: [KitPanel],
  template: `
    <fleet-kit-panel
      class="fill"
      aria-label="Event log"
      data-testid="event-log-panel"
      label="Event log"
    >
      @if (rows().length === 0) {
        <p class="none" data-testid="event-log-empty">No events yet.</p>
      } @else {
        <div class="rows" data-testid="event-log-rows">
          @for (row of rows(); track row.seq) {
            <div class="ev" data-testid="event-log-row" [attr.data-kind]="row.type">
              <span class="t" data-testid="event-log-time">{{ row.time }}</span>
              <span class="m" data-testid="event-log-message">{{ row.message }}</span>
            </div>
          }
        </div>
      }
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
    .none {
      color: var(--label-dim);
      font-size: var(--fs-xs);
      padding: 6px 8px;
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
    .ev .m {
      color: var(--text);
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
        message: summarize(event),
      }))
      .reverse(),
  );
}

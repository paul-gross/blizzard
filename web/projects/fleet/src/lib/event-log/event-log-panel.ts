import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { compactRef } from '../compact-ref';
import { KitPanel } from '../kit/kit-panel';
import { FleetLiveUpdates, type LoggedEvent } from '../sse/fleet-live';
import { formatClockTime } from '../when';

/** One rendered Event log row — the logged frame plus its display strings. */
interface LogRow {
  readonly seq: number;
  readonly type: string;
  readonly time: string;
  readonly message: string;
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
    case 'decision-opened':
      return `${chunk} gate opened`;
    case 'decision-resolved':
      return `${chunk} gate resolved`;
    case 'queue-changed':
      return 'ready queue changed';
    case 'runner-changed':
      return `runner ${compactRef(event.data.runner_id ?? '—')} changed`;
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
      [count]="rows().length + ' ev'"
      countTestid="event-log-count"
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
      grid-template-columns: 58px 1fr;
      gap: 6px;
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

import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { FleetLiveUpdates, type LoggedEvent } from '../sse/fleet-live';

/** One rendered Event log row — the logged frame plus its display strings. */
interface LogRow {
  readonly seq: number;
  readonly type: string;
  readonly time: string;
  readonly message: string;
}

/** Zero-padded `HH:MM:SS` for a row's arrival time — legible and locale-stable. */
function clockTime(at: number): string {
  const d = new Date(at);
  const pad = (n: number): string => `${n}`.padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** A short, legible chunk/runner id — the leading segment operators recognize. */
function short(id: string): string {
  return id.slice(0, 12);
}

/**
 * A human-readable one-line summary of a hub event (issue #25 — "a legible summary").
 * Maps the board's live vocabulary (events/broker.py) onto plain phrasing; an unknown
 * type degrades to its raw name rather than dropping the row.
 */
function summarize(event: LoggedEvent): string {
  const chunk = event.data.chunk_id ? short(event.data.chunk_id) : '';
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
      return `runner ${short(event.data.runner_id ?? '—')} changed`;
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
  template: `
    <section class="panel log-panel" aria-label="Event log" data-testid="event-log-panel">
      <div class="panel-head">
        <span class="lbl">Event log</span>
        <span class="lbl" data-testid="event-log-count">{{ rows().length }} ev</span>
      </div>
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
    </section>
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
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .panel {
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
      border: 1px solid var(--bezel);
      display: flex;
      flex-direction: column;
      min-height: 0;
      flex: 1;
    }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.25);
      flex: none;
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
      .map((event) => ({ seq: event.seq, type: event.type, time: clockTime(event.at), message: summarize(event) }))
      .reverse(),
  );
}

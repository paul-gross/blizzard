import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitPanel } from '../kit/kit-panel';

/** One rendered Event log row — the logged frame plus its display strings.
 * `detail` is the block row's second line (`chunk-changed` only, issue #212); every
 * other event type leaves it unset and renders as the single-line row it always has. */
export interface LogRow {
  readonly seq: number;
  readonly type: string;
  readonly time: string;
  readonly message: string;
  readonly detail?: string;
}

/**
 * The Event log panel's presentational half (issue #213 Phase 4 — split from
 * `event-log-panel.ts`, `bzh:frontend-container-presentational`) — a scrolling,
 * newest-first feed of recent fleet events with a running count.
 *
 * Renders exactly the rows and async state it is handed; injects no query or live
 * spine of its own. All color comes from the design-token layer (design/tokens.css),
 * never hard-coded hex.
 */
@Component({
  selector: 'fleet-event-log-view',
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
export class EventLogView {
  /** The feed newest-first, already shaped into display rows. */
  readonly rows = input.required<readonly LogRow[]>();

  /** The panel's async state (AC — loading vs. empty vs. error vs. ready). */
  readonly state = input.required<KitAsyncStateValue>();
}

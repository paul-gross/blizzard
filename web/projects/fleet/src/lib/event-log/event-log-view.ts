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
  templateUrl: './event-log-view.html',
  styleUrl: './event-log-view.css',
})
export class EventLogView {
  /** The feed newest-first, already shaped into display rows. */
  readonly rows = input.required<readonly LogRow[]>();

  /** The panel's async state (AC — loading vs. empty vs. error vs. ready). */
  readonly state = input.required<KitAsyncStateValue>();
}

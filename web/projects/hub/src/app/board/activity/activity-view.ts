import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue, KitPanel } from 'fleet';

/** One rendered Activity feed row — the logged frame plus its display strings.
 * `detail` is the row's optional second line. */
export interface ActivityRow {
  readonly seq: number;
  readonly type: string;
  readonly time: string;
  readonly message: string;
  readonly detail?: string;
}

/**
 * The Activity feed panel's presentational half (`bzh:frontend-container-presentational`) — a scrolling,
 * newest-first feed of recent fleet events with a running count.
 *
 * Renders exactly the rows and async state it is handed; injects no query or live
 * spine of its own. All color comes from the design-token layer (design/tokens.css),
 * never hard-coded hex.
 */
@Component({
  selector: 'app-activity-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel],
  templateUrl: './activity-view.html',
  styleUrl: './activity-view.css',
})
export class ActivityFeedView {
  /** The feed newest-first, already shaped into display rows. */
  readonly rows = input.required<readonly ActivityRow[]>();

  /** The panel's async state — loading, empty, error, or ready. */
  readonly state = input.required<KitAsyncStateValue>();
}

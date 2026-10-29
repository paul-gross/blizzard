import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue, KitButton } from 'fleet';
import type { FlowRecordRow } from './flow-record.model';

/**
 * A flow's record in the dock: every fact, newest first — each submission, turn and how it ended, landing, and
 * withdrawal, by chunk — with a control to read older facts while there are any.
 *
 * Presentational only: a chunk's click leaves as {@link selectChunk}.
 */
@Component({
  selector: 'app-flow-record-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitButton],
  templateUrl: './flow-record-view.html',
  styleUrl: './flow-record-view.css',
})
export class FlowRecordView {
  readonly title = input.required<string>();
  readonly rows = input.required<readonly FlowRecordRow[]>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly hasOlder = input(false);
  readonly olderPending = input(false);

  readonly older = output<void>();
  readonly dismiss = output<void>();
  readonly selectChunk = output<string>();
}

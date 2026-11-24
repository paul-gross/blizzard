import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { FleetWhen, KitAsyncState, type KitAsyncStateValue, KitButton, KitPanel } from 'fleet';

import type { SettingsChangeVm } from './settings.model';

/** One record's change log, newest first: each change's revision, op, door, actor, and time, the record it moved
 * when the log spans several, and each moved key's old and new value; Load more reads the next older page. */
@Component({
  selector: 'app-settings-history-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState, KitButton, KitPanel],
  templateUrl: './settings-history-view.html',
  styleUrl: './settings-history-view.css',
})
export class SettingsHistoryView {
  readonly title = input.required<string>();
  /** The record's id, the prefix of its test ids: `settings-history-<recordId>-…`. */
  readonly recordId = input.required<string>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly changes = input.required<readonly SettingsChangeVm[]>();
  readonly hasNext = input(false);
  readonly loadingNext = input(false);

  readonly loadMore = output<void>();
}

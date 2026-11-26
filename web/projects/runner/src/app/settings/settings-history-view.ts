import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { FleetWhen } from 'fleet/core';
import { KitAsyncState, type KitAsyncStateValue, KitButton, KitErrorLine, KitPanel } from 'fleet/kit';

import type { SettingsChangeVm } from './settings.model';

/** One record's change log, newest first: each change's revision, op, door, actor, and time, and each moved key's
 * old and new value; Load more reads the next older page. A log whose runner is not served says why instead. */
@Component({
  selector: 'app-settings-history-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState, KitButton, KitErrorLine, KitPanel],
  templateUrl: './settings-history-view.html',
  styleUrl: './settings-history-view.css',
})
export class SettingsHistoryView {
  readonly title = input.required<string>();
  /** The record's id, the prefix of its test ids: `settings-history-<recordId>-…`. */
  readonly recordId = input.required<string>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly changes = input.required<readonly SettingsChangeVm[]>();
  /** Why the log cannot be read, shown in place of its read; `null` while it can be. */
  readonly unserved = input<string | null>(null);
  readonly hasNext = input(false);
  readonly loadingNext = input(false);

  readonly loadMore = output<void>();
}

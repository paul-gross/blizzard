import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { FleetWhen, KitAsyncState, type KitAsyncStateValue, KitBadge, KitButton, KitErrorLine, KitPanel, KitSectionHead } from 'fleet';

import type { InForceLineVm, SettingGroupVm } from './settings.model';
import { SettingsInForce } from './settings-in-force';

/** One settings record's panel: its `in_force` line on top, then every key grouped by table with its value, source,
 * and lifetime badge, an Edit control on each key the session may edit. A record whose keys could not be resolved
 * shows why in their place. */
@Component({
  selector: 'app-settings-record-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState, KitBadge, KitButton, KitErrorLine, KitPanel, KitSectionHead, SettingsInForce],
  templateUrl: './settings-record-view.html',
  styleUrl: './settings-record-view.css',
})
export class SettingsRecordView {
  readonly title = input.required<string>();
  /** The record's id, the prefix of its test ids: `settings-<recordId>-…`. */
  readonly recordId = input.required<string>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly line = input.required<InForceLineVm | null>();
  readonly groups = input.required<readonly SettingGroupVm[]>();
  readonly settingsError = input<string | null>(null);
  /** Holds every Edit control off while this record's write is in flight. */
  readonly editDisabled = input(false);

  /** The key whose Edit control was pressed. */
  readonly edit = output<string>();
}

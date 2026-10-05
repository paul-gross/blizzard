import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FleetWhen, KitAsyncState, type KitAsyncStateValue } from 'fleet';

import { ConfigFieldDiff } from '../config-field-diff';
import type { ChangeEntryVm } from './changes.model';

/**
 * A change log entry's detail — who made it, through which door, and when, then each
 * change it holds with its field diff (or, for a change with no fields, the revision
 * step it made). Presentational.
 */
@Component({
  selector: 'app-change-entry-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldDiff, FleetWhen, KitAsyncState, RouterLink],
  templateUrl: './change-entry-panel.html',
  styleUrl: './change-entry-panel.css',
})
export class ChangeEntryPanel {
  readonly vm = input.required<ChangeEntryVm | null>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly emptyText = input.required<string>();
}

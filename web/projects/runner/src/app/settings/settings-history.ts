import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { asyncState } from 'fleet';

import { settingsChanges, type SettingsRecordRef } from './settings.model';
import { injectSettingsChangesQuery } from './settings.query';
import { SettingsHistoryView } from './settings-history-view';

/** One settings record's change log, read a page at a time. */
@Component({
  selector: 'app-settings-history',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [SettingsHistoryView],
  templateUrl: './settings-history.html',
})
export class SettingsHistory {
  readonly ref = input.required<SettingsRecordRef>();

  protected readonly query = injectSettingsChangesQuery(() => this.ref().target);
  protected readonly changes = computed(() => settingsChanges(this.query.data()?.pages));
  protected readonly state = computed(() => asyncState(this.query, this.changes().length === 0));
}

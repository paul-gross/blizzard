import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { asyncState } from 'fleet/core';

import type { HistoryTarget } from './settings.domain';
import { settingsChanges } from './settings.model';
import { injectSettingsChangesQuery } from './settings.query';
import { SettingsHistoryView } from './settings-history-view';

/** Whose change log a History panel shows: its title, the id its test ids are prefixed by, and the log it reads. */
export interface HistoryRef {
  readonly id: string;
  readonly title: string;
  readonly target: HistoryTarget;
  /** Why the log cannot be read — its runner is not served — `null` once it can. */
  readonly unserved?: string | null;
}

/** One record's change log, read a page at a time. */
@Component({
  selector: 'app-settings-history',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [SettingsHistoryView],
  templateUrl: './settings-history.html',
})
export class SettingsHistory {
  readonly ref = input.required<HistoryRef>();

  protected readonly query = injectSettingsChangesQuery(
    () => this.ref().target,
    () => (this.ref().unserved ?? null) === null,
  );
  protected readonly changes = computed(() => settingsChanges(this.query.data()?.pages));
  protected readonly state = computed(() => asyncState(this.query, this.changes().length === 0));
}

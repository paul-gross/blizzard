import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitAsyncState, KitButton, KitSectionHead, type KitAsyncStateValue, type HubSettingRow } from 'fleet';
import { ConfigRecordPanel } from '../config-record-panel';
import { ConfigRevisions } from '../config-revisions';
import { ChangeEntryPanel } from '../changes/change-entry-panel';
import type { ChangeEntryVm } from '../changes/changes.model';
import type { RevisionRowVm } from '../config-history.model';
import { recordOf } from './hub-settings.model';

@Component({ selector: 'app-hub-settings-view', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitButton, KitSectionHead, ChangeEntryPanel, ConfigRecordPanel, ConfigRevisions], templateUrl: './hub-settings-view.html', styleUrl: './hub-settings-view.css' })
export class HubSettingsView {
  readonly allowed = input.required<boolean>();
  readonly identityPending = input.required<boolean>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly rows = input.required<readonly HubSettingRow[]>();
  readonly busy = input.required<boolean>();
  readonly revisions = input.required<readonly RevisionRowVm[]>();
  readonly historyState = input.required<KitAsyncStateValue>();
  readonly entries = input.required<readonly ChangeEntryVm[]>();
  readonly hasNext = input.required<boolean>();
  readonly loadingNext = input.required<boolean>();
  readonly edit = output<HubSettingRow>();
  readonly loadMore = output<void>();
  protected readonly record = recordOf;
}

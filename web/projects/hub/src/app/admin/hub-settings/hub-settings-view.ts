import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitAsyncState, KitButton, KitErrorLine, KitTextInput, type KitAsyncStateValue, type HubSettingRow, type HubSettingsChange } from 'fleet';
import { ConfigRecordPanel } from '../config-record-panel';
import { ConfigRevisions } from '../config-revisions';
import { ConfigFieldDiff } from '../config-field-diff';
import type { RevisionRowVm } from '../config-history.model';
import { diffOf, recordOf } from './hub-settings.model';

@Component({ selector: 'app-hub-settings-view', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitButton, KitErrorLine, KitTextInput, ConfigRecordPanel, ConfigRevisions, ConfigFieldDiff], templateUrl: './hub-settings-view.html' })
export class HubSettingsView {
  readonly allowed = input.required<boolean>();
  readonly identityPending = input.required<boolean>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly rows = input.required<readonly HubSettingRow[]>();
  readonly editing = input.required<{ row: HubSettingRow; revision: number } | null>();
  readonly value = input.required<string>();
  readonly failure = input.required<string | null>();
  readonly busy = input.required<boolean>();
  readonly revisions = input.required<readonly RevisionRowVm[]>();
  readonly historyState = input.required<KitAsyncStateValue>();
  readonly changes = input.required<readonly HubSettingsChange[]>();
  readonly hasNext = input.required<boolean>();
  readonly loadingNext = input.required<boolean>();
  readonly edit = output<HubSettingRow>();
  readonly valueChange = output<string>();
  readonly save = output<boolean>();
  readonly cancelled = output<void>();
  readonly loadMore = output<void>();
  protected readonly record = recordOf;
  protected readonly diff = diffOf;
}

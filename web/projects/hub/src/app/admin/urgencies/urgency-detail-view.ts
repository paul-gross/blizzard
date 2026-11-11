import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitButton, KitDialog, KitErrorLine, KitSelect, type KitAsyncStateValue, type UrgencyView } from 'fleet';
import type { ConfigActionsVm } from '../config-actions.model';
import type { ConfigFormValues } from '../config-edit.model';
import { ConfigFormDialog } from '../config-form-dialog';
import type { LastChangeVm, RevisionRowVm } from '../config-history.model';
import { ConfigRecordPanel, type ConfigRecordVm } from '../config-record-panel';
import { URGENCY_FIELDS } from './urgencies.model';

@Component({ selector: 'app-urgency-detail-view', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFormDialog, ConfigRecordPanel, KitButton, KitDialog, KitErrorLine, KitSelect],
  templateUrl: './urgency-detail-view.html', styleUrl: './urgency-detail.css' })
export class UrgencyDetailView {
  readonly vm = input.required<ConfigRecordVm | null>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly lastChange = input<LastChangeVm | null>(null);
  readonly changes = input<readonly RevisionRowVm[]>([]);
  readonly changesState = input.required<KitAsyncStateValue>();
  readonly cli = input<string | null>(null);
  readonly actions = input<ConfigActionsVm | null>(null);
  readonly busy = input(false);
  readonly actionError = input<string | null>(null);
  readonly mobile = input(false);
  readonly record = input<UrgencyView | undefined>();
  readonly editInitial = input<ConfigFormValues | null>(null);
  readonly editOpen = input(false);
  readonly editError = input<string | null>(null);
  readonly editPending = input(false);
  readonly retireSnapshot = input<{ key: string; revision: number; openChunks: number } | null>(null);
  readonly destination = input('');
  readonly destinationOptions = input.required<readonly { value: string; label: string }[]>();
  readonly edit = output<void>();
  readonly lifecycle = output<'retire' | 'enable'>();
  readonly save = output<ConfigFormValues>();
  readonly closeEdit = output<void>();
  readonly closeRetire = output<void>();
  readonly retire = output<void>();
  readonly destinationChange = output<string>();
  protected readonly fields = URGENCY_FIELDS;
}

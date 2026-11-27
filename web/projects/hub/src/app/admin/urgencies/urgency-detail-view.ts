import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { type Urgency } from 'fleet/core';
import { KitButton, KitErrorLine, type KitAsyncStateValue } from 'fleet/kit';
import { KitDialog } from 'fleet/kit/dialog';
import { KitSelect } from 'fleet/kit/select';
import type { ConfigActionsVm } from '../config-actions.model';
import type { FieldTree } from '@angular/forms/signals';
import type { UrgencyDraft } from './urgency.domain';
import { ConfigFieldTreeDialog } from '../config-field-tree-dialog';
import type { LastChangeVm, RevisionRowVm } from '../config-history.model';
import { ConfigRecordPanel, type ConfigRecordVm } from '../config-record-panel';
import { URGENCY_FIELDS } from './urgency.form';

@Component({ selector: 'app-urgency-detail-view', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldTreeDialog, ConfigRecordPanel, KitButton, KitDialog, KitErrorLine, KitSelect],
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
  readonly imageError = input<string | null>(null);
  readonly mobile = input(false);
  readonly record = input<Urgency | undefined>();
  readonly editForm = input.required<FieldTree<UrgencyDraft>>();
  readonly editBaseline = input<UrgencyDraft | null>(null);
  readonly editOpen = input(false);
  readonly retireSnapshot = input<{ key: string; revision: number; openChunks: number } | null>(null);
  readonly destination = input('');
  readonly destinationOptions = input.required<readonly { value: string; label: string }[]>();
  readonly edit = output<void>();
  readonly lifecycle = output<'retire' | 'enable'>();
  readonly save = output<void>();
  readonly closeEdit = output<void>();
  readonly closeRetire = output<void>();
  readonly retire = output<void>();
  readonly destinationChange = output<string>();
  protected readonly fields = URGENCY_FIELDS;
}

import { CdkDrag, CdkDragHandle, CdkDropList, type CdkDragDrop } from '@angular/cdk/drag-drop';
import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitAsyncState, KitButton, KitChips, KitErrorLine, KitSelect, KitSelectRow, type KitAsyncStateValue, type UrgencyView } from 'fleet';
import { UrgencyPill } from '../../core/urgency-pill';
import { LIFECYCLE_FILTER_OPTIONS, type LifecycleFilter } from '../config-filter.model';
import { ConfigFormDialog } from '../config-form-dialog';
import type { ConfigFormValues } from '../config-edit.model';
import { URGENCY_FIELDS } from './urgencies.model';

@Component({
  selector: 'app-urgencies-list', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkDrag, CdkDragHandle, CdkDropList, ConfigFormDialog, KitAsyncState, KitButton, KitChips, KitErrorLine, KitSelect, KitSelectRow, UrgencyPill],
  templateUrl: './urgencies-list.html', styleUrl: './urgencies-page.css',
})
export class UrgenciesList {
  readonly canEdit = input.required<boolean>();
  readonly mobile = input.required<boolean>();
  readonly pending = input.required<boolean>();
  readonly settingsReady = input.required<boolean>();
  readonly defaultKey = input<string | null>(null);
  readonly defaultOptions = input.required<readonly { value: string; label: string }[]>();
  readonly error = input<string | null>(null);
  readonly lifecycle = input.required<LifecycleFilter>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly rows = input.required<readonly UrgencyView[]>();
  readonly selected = input<string | null>(null);
  readonly createOpen = input(false);
  readonly createError = input<string | null>(null);
  readonly createPending = input(false);
  readonly create = output<void>();
  readonly closeCreate = output<void>();
  readonly submitCreate = output<ConfigFormValues>();
  readonly changeDefault = output<string>();
  readonly filter = output<string>();
  readonly pick = output<string>();
  readonly reorder = output<CdkDragDrop<readonly UrgencyView[]>>();
  protected readonly options = LIFECYCLE_FILTER_OPTIONS;
  protected readonly fields = URGENCY_FIELDS;
}

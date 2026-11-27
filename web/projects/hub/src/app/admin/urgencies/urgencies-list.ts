import { CdkDrag, CdkDragHandle, CdkDropList, type CdkDragDrop } from '@angular/cdk/drag-drop';
import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { type Urgency } from 'fleet/core';
import { KitAsyncState, KitButton, KitErrorLine, type KitAsyncStateValue } from 'fleet/kit';
import { KitChips } from 'fleet/kit/chips';
import { KitSelect } from 'fleet/kit/select';
import { KitSelectList, KitSelectRow } from 'fleet/kit/select-list';
import { UrgencyPill } from '../../core/urgency-pill';
import { LIFECYCLE_FILTER_OPTIONS, type LifecycleFilter } from '../config-filter.model';
import { type FieldTree } from '@angular/forms/signals';
import { ConfigFieldTreeDialog } from '../config-field-tree-dialog';
import type { UrgencyDraft } from './urgency.domain';
import { URGENCY_FIELDS } from './urgency.form';

@Component({
  selector: 'app-urgencies-list', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkDrag, CdkDragHandle, CdkDropList, ConfigFieldTreeDialog, KitAsyncState, KitButton, KitChips, KitErrorLine, KitSelect, KitSelectList, KitSelectRow, UrgencyPill],
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
  readonly rows = input.required<readonly Urgency[]>();
  readonly selected = input<string | null>(null);
  readonly createOpen = input(false);
  readonly createForm = input.required<FieldTree<UrgencyDraft>>();
  readonly create = output<void>();
  readonly closeCreate = output<void>();
  readonly submitCreate = output<void>();
  readonly changeDefault = output<string>();
  readonly filter = output<string>();
  readonly pick = output<string>();
  readonly reorder = output<CdkDragDrop<readonly Urgency[]>>();
  protected readonly options = LIFECYCLE_FILTER_OPTIONS;
  protected readonly fields = URGENCY_FIELDS;
}

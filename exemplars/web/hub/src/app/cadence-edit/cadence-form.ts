// exemplar: bzh:frontend-edit-baseline — presentational: it renders the field tree its container owns.

import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';

import { type KitChipOption, KitField, KitForm, KitSelect, KitTextInput } from 'fleet';
import { CADENCE_ANCHORS, type CadenceDraft } from './cadence.domain';

const ANCHOR_OPTIONS: readonly KitChipOption[] = [
  { value: 'unstated', label: 'unstated (started)' },
  ...CADENCE_ANCHORS.map((anchor) => ({ value: anchor, label: anchor })),
];

/** A cadence's name and rhythm as an edit form. */
@Component({
  selector: 'app-cadence-edit-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitField, KitForm, KitSelect, KitTextInput],
  templateUrl: './cadence-form.html',
})
export class CadenceEditForm {
  readonly form = input.required<FieldTree<CadenceDraft>>();

  readonly save = output<void>();
  // Named `cancel` as the forms spec declares, matching the kit form it forwards.
  // eslint-disable-next-line @angular-eslint/no-output-native
  readonly cancel = output<void>();

  protected readonly anchorOptions = ANCHOR_OPTIONS;
}

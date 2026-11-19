import { ChangeDetectionStrategy, Component, inject, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';

import { FLEET_CLOCK, type KitChipOption, KitCombobox, KitField, KitForm, KitSelect, KitTextInput, zoneOptions } from 'fleet';

import { CADENCE_ANCHORS, type CadenceDraft } from '../core/cadence.domain';

const ANCHOR_OPTIONS: readonly KitChipOption[] = [
  { value: 'unstated', label: 'unstated (started)' },
  ...CADENCE_ANCHORS.map((anchor) => ({ value: anchor, label: anchor })),
];

/**
 * A cadence's name and rhythm as a form — the create form, which also takes the slug, or the edit form, where the slug
 * is fixed. Presentational: it renders the field tree it is handed, and its container owns the draft, the rules, and
 * the save.
 */
@Component({
  selector: 'app-cadence-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitCombobox, KitField, KitForm, KitSelect, KitTextInput],
  templateUrl: './cadence-form.html',
  styleUrl: './cadence-form.css',
})
export class FleetCadenceForm {
  readonly form = input.required<FieldTree<CadenceDraft>>();
  /** Whether the form creates a cadence — only then is the slug typed. */
  readonly creating = input(false);
  /** The prefix every field's test id carries. */
  readonly testid = input('gardening-cadence-form');

  readonly save = output<void>();
  // Named `cancel` as the forms spec declares, matching the kit form it forwards.
  // eslint-disable-next-line @angular-eslint/no-output-native
  readonly cancel = output<void>();

  protected readonly anchorOptions = ANCHOR_OPTIONS;
  /** The browser's zones with their offsets as of opening the form. */
  protected readonly zones = zoneOptions(inject(FLEET_CLOCK)());
}

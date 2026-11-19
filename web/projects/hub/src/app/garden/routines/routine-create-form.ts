import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import { KitField, KitForm, KitSelect, KitTextInput, type UrgencyView } from 'fleet';

import { urgencyOptions } from '../../core/urgency-options.model';
import type { RoutineCreateDraft } from '../core/routine.domain';

/**
 * A new routine as a form. Presentational: it renders the field tree it is handed, and its container owns the draft,
 * the rules, and the save.
 */
@Component({
  selector: 'app-routine-create-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitField, KitForm, KitSelect, KitTextInput],
  templateUrl: './routine-create-form.html',
  styleUrl: './routine-create-form.css',
})
export class RoutineCreateForm {
  readonly form = input.required<FieldTree<RoutineCreateDraft>>();
  readonly urgencies = input<readonly UrgencyView[]>([]);
  /** The prefix every field's test id carries. */
  readonly testid = input('gardening-routine-create');

  readonly save = output<void>();
  // Named `cancel` as the forms spec declares, matching the kit form it forwards.
  // eslint-disable-next-line @angular-eslint/no-output-native
  readonly cancel = output<void>();

  protected readonly options = computed(() =>
    urgencyOptions(this.urgencies(), {
      key: 'slug',
      retired: 'exclude',
      inherit: { label: 'Tenant default', name: this.urgencies().find((u) => u.default)?.name ?? null },
    }),
  );
}

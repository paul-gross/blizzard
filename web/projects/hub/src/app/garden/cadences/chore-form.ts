import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';

import { KitCheckbox, KitField, KitForm, KitOption, KitOptionGroup, KitSelect, KitTextInput, type UrgencyView } from 'fleet';

import type { ChoreDraft } from '../core/cadence.domain';
import { urgencyOptions } from '../../core/urgency-options.model';
import { choreModeLabel } from './cadence-vocabulary';

/**
 * One chore as a form — adding a chore, or editing one in place. Presentational: it renders the field tree it is
 * handed, and its container owns the draft, the rules, and the save. A mode this build does not know is offered as
 * itself ({@link heldMode}), so saving an untouched chore never rewrites it.
 */
@Component({
  selector: 'app-chore-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitCheckbox, KitField, KitForm, KitOption, KitOptionGroup, KitSelect, KitTextInput],
  templateUrl: './chore-form.html',
  styleUrl: './chore-form.css',
})
export class FleetChoreForm {
  readonly form = input.required<FieldTree<ChoreDraft>>();
  /** Whether the form adds a chore — only then is the key typed; a chore keeps its key once written. */
  readonly adding = input(false);
  readonly urgencies = input<readonly UrgencyView[]>([]);
  /** The mode the chore held when the form opened — kept on offer after another is picked. */
  readonly heldMode = input<string | null>(null);
  readonly testid = input('gardening-chore-form');

  readonly save = output<void>();
  // Named `cancel` as the forms spec declares, matching the kit form it forwards.
  // eslint-disable-next-line @angular-eslint/no-output-native
  readonly cancel = output<void>();

  /** The known modes, and the held one when this build does not know it. */
  protected readonly modes = computed<readonly { value: string; label: string }[]>(() => {
    const known = choreModeLabel.keys.map((mode) => ({ value: mode, label: choreModeLabel(mode) }));
    const held = this.heldMode();
    return held === null || choreModeLabel.has(held) ? known : [...known, { value: held, label: held }];
  });

  protected readonly urgencyOptions = computed(() =>
    urgencyOptions(this.urgencies(), {
      key: 'slug',
      retired: { current: this.form().urgency().value() || null },
      inherit: { label: 'Routine default', name: null },
    }),
  );
}

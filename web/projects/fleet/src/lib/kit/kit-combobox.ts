import { Combobox, ComboboxPopup, ComboboxWidget } from '@angular/aria/combobox';
import { Listbox, Option } from '@angular/aria/listbox';
import { CdkConnectedOverlay } from '@angular/cdk/overlay';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  input,
  model,
  output,
  signal,
  untracked,
} from '@angular/core';
import type { FormValueControl } from '@angular/forms/signals';

import { KIT_FIELD_LABEL } from './kit-field-label';

/** One pickable row of a {@link KitCombobox}. */
export interface ComboOption {
  readonly value: string;
  readonly label: string;
  /** Shown at the row's end, such as a UTC offset. */
  readonly meta?: string;
}

/** The label of a current value that no option carries, listed first so the control still shows it. */
export const NOT_IN_LIST = 'not in list';

/**
 * A pick-only combobox — the control for a long closed vocabulary (the time zones), where the
 * text box only searches and the value is always one of the options. Typing filters the options
 * by case-insensitive substring of their label; Up and Down move the highlight, Enter picks it,
 * Escape closes. Leaving the box with text that picked nothing restores the text of the current
 * value.
 *
 * A current value with no option is listed first as its own option, marked "not in list", and kept
 * until another is picked. With `noneLabel`, a first option of that label whose value is `''` lets
 * an optional field be cleared.
 *
 * Built on the Angular Aria combobox over its listbox; the popup rides a CDK overlay so a dialog or
 * a scrolling form cannot clip it.
 */
@Component({
  selector: 'fleet-kit-combobox',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [Combobox, ComboboxPopup, ComboboxWidget, Listbox, Option, CdkConnectedOverlay],
  templateUrl: './kit-combobox.html',
  styleUrl: './kit-combobox.css',
})
export class KitCombobox implements FormValueControl<string> {
  /** The picked value; a `[formField]` binds it. */
  readonly value = model('');

  readonly disabled = input(false);

  readonly options = input.required<readonly ComboOption[]>();

  /** Names the box and its listbox, for a control with no visible `<label>` of its own. */
  readonly ariaLabel = input<string | null>(null);

  /** Lists a first option of this label whose value is `''`, so an optional field can be cleared. */
  readonly noneLabel = input<string | null>(null);

  readonly testid = input<string | null>(null);

  /** Emits on native blur, so a bound field becomes touched. */
  readonly touch = output<void>();

  protected readonly fieldLabel = inject(KIT_FIELD_LABEL, { optional: true });

  /** The box's text — the search while open, the current label while closed. */
  protected readonly text = signal('');
  protected readonly expanded = signal(false);
  /** True once the user has typed since opening: only then does the text filter. */
  protected readonly edited = signal(false);
  /** Kept empty so a re-pick of the current row still reports; the current row is marked by class. */
  protected readonly picked = signal<string[]>([]);

  private readonly all = computed<readonly ComboOption[]>(() => {
    const value = this.value();
    const none = this.noneLabel();
    const rows = none === null ? this.options() : [{ value: '', label: none }, ...this.options()];
    if (value === '' || rows.some((o) => o.value === value)) return rows;
    return [{ value, label: value, meta: NOT_IN_LIST }, ...rows];
  });

  private readonly currentLabel = computed(() => {
    const value = this.value();
    return this.all().find((o) => o.value === value)?.label ?? value;
  });

  protected readonly shown = computed(() => {
    const rows = this.all();
    if (!this.edited()) return rows;
    const needle = this.text().toLowerCase();
    return rows.filter((o) => o.label.toLowerCase().includes(needle));
  });

  constructor() {
    // Closed (or the value changed from outside), the box shows the current value's label.
    effect(() => {
      const label = this.currentLabel();
      if (!this.expanded()) {
        untracked(() => {
          this.text.set(label);
          this.edited.set(false);
        });
      }
    });
  }

  protected onListChange(picked: string[]): void {
    const value = picked[0];
    this.picked.set([]);
    if (value === undefined) return;
    this.value.set(value);
    this.expanded.set(false);
  }
}

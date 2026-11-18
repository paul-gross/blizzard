import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, input, model, output } from '@angular/core';
import type { FormValueControl } from '@angular/forms/signals';

import { KIT_FIELD_LABEL } from './kit-field-label';

/**
 * A native radio group: the `role="radiogroup"` host that projects {@link KitOption}s. Every radio shares one native
 * `name` — the bound field's, else a generated id — so arrow-key navigation is the browser's own model for same-name
 * radios and this adds no keyboard handling. The checked radio is the option whose `value` equals the group's `value`.
 * Works as a plain `[value]`/`(valueChange)` group, or under a `[formField]`.
 */
@Component({
  selector: 'fleet-kit-option-group',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-option-group.html',
  host: {
    role: 'radiogroup',
    '[attr.aria-label]': 'ariaLabel()',
    '[attr.aria-labelledby]': 'ariaLabel() ? null : (fieldLabel?.labelId ?? null)',
    '(focusout)': 'onFocusOut($event)',
  },
})
export class KitOptionGroup implements FormValueControl<string> {
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  protected readonly fieldLabel = inject(KIT_FIELD_LABEL, { optional: true });

  /** The checked option's `value`. */
  readonly value = model('');
  readonly ariaLabel = input<string | null>(null);

  /** The shared native name; a bound field supplies it. */
  readonly name = input('');
  readonly disabled = input(false);

  /** Emits when focus leaves the group, so a bound field becomes touched. */
  readonly touch = output<void>();

  private readonly generatedName = `fleet-kit-option-group-${nextId++}`;

  /** The `name` every projected radio carries. */
  readonly radioName = computed(() => this.name() || this.generatedName);

  /** Focuses the checked radio, or the first enabled one. */
  focus(options?: FocusOptions): void {
    const el = this.host.nativeElement;
    const target =
      el.querySelector<HTMLInputElement>('input[type="radio"]:checked:not(:disabled)') ??
      el.querySelector<HTMLInputElement>('input[type="radio"]:not(:disabled)');
    target?.focus(options);
  }

  protected onFocusOut(event: FocusEvent): void {
    const next = event.relatedTarget as Node | null;
    if (next && this.host.nativeElement.contains(next)) return;
    this.touch.emit();
  }
}

let nextId = 0;

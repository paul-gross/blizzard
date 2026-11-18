import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
} from '@angular/core';
import type { FieldTree } from '@angular/forms/signals';

import { KIT_FIELD_LABEL, type KitFieldLabel } from './kit-field-label';
import { KitErrorLine } from './kit-error-line';

let nextId = 0;

/** An error's words: its message, else a plain statement of the built-in rule that carries none (`required`). */
export function errorText(error: {
  readonly kind: string;
  readonly message?: string;
}): string {
  return error.message || (error.kind === 'required' ? 'Required' : 'Invalid');
}

/**
 * One form field's chrome: the label, a required marker from the field's own `required()` state, the hint, and the
 * field's errors once it is touched (`product:immediate-validation`, `product:error-at-field`). The projected control
 * is named by the label through `aria-labelledby`, not a wrapping `<label>` — a wrapping label would activate the first
 * radio of a projected option group on a click.
 */
@Component({
  selector: 'fleet-kit-field',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitErrorLine],
  templateUrl: './kit-field.html',
  styleUrl: './kit-field.css',
  providers: [
    {
      provide: KIT_FIELD_LABEL,
      useFactory: (): KitFieldLabel => ({
        labelId: `fleet-kit-field-label-${nextId++}`,
      }),
    },
  ],
})
export class KitField {
  readonly label = input.required<string>();
  readonly field = input.required<FieldTree<unknown>>();
  readonly hint = input<string | null>(null);

  protected readonly labelId = inject(KIT_FIELD_LABEL).labelId;

  protected readonly required = computed(() => this.field()().required());

  /** Errors show only after the field is touched; submit touches every field. */
  protected readonly messages = computed(() => {
    const state = this.field()();
    return state.touched()
      ? state
          .errors()
          .map((error) => errorText(error))
          .filter((m) => m !== '')
      : [];
  });
}

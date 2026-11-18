import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  output,
} from '@angular/core';
import type { FieldTree } from '@angular/forms/signals';

import { KitButton } from './kit-button';
import { KitErrorLine } from './kit-error-line';
import { errorText } from './kit-field';

/**
 * A form's chrome: a native `<form novalidate>` projecting the field stack, the root field's own errors as the form
 * line, and the Save / Cancel action row. Save is the form's submit button and is disabled while the form is
 * submitting; the native `submit` is default-prevented and emitted once as `save`. This part never calls `submit()` —
 * the container answers `save` with `submitForm`.
 */
@Component({
  selector: 'fleet-kit-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitErrorLine],
  templateUrl: './kit-form.html',
  styleUrl: './kit-form.css',
})
export class KitForm {
  readonly form = input.required<FieldTree<unknown>>();
  readonly saveLabel = input('Save');
  readonly cancelLabel = input('Cancel');
  /** Holds Save off for a reason the form's own state does not carry (nothing changed yet, a file still being read). ORed with `submitting()`. */
  readonly saveDisabled = input(false);
  /** The prefix of the form's own test ids: `<testid>-save`, `<testid>-cancel`, and `<testid>-line`. */
  readonly testid = input('kit-form');

  readonly save = output<void>();
  // Named `cancel` as the forms spec declares; a view binds `(cancel)`, which on this component is this output.
  // eslint-disable-next-line @angular-eslint/no-output-native
  readonly cancel = output<void>();

  protected readonly submitting = computed(() => this.form()().submitting());

  /** The root's own errors, once the root is touched (self or any child touched). */
  protected readonly messages = computed(() => {
    const state = this.form()();
    return state.touched()
      ? state
          .errors()
          .map((error) => errorText(error))
          .filter((m) => m !== '')
      : [];
  });

  protected onSubmit(event: Event): void {
    event.preventDefault();
    this.save.emit();
  }
}

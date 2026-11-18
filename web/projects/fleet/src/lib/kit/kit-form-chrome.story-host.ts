import { Component, input, signal } from '@angular/core';
import {
  type FieldTree,
  FormField,
  type ValidationError,
  disabled,
  form,
  required,
  schema,
  submit,
  validate,
} from '@angular/forms/signals';

import { KitField } from './kit-field';
import { KitForm } from './kit-form';
import { KitNumberInput } from './kit-number-input';
import { KitOrDefault } from './kit-or-default';
import { KitTextInput } from './kit-text-input';

export interface Draft {
  slug: string;
  useDefault: boolean;
  hours: string;
}

const rules = schema<Draft>((p) => {
  required(p.slug);
  disabled(p.hours, ({ valueOf }) => valueOf(p.useDefault));
  validate(p.hours, ({ value, valueOf }) =>
    valueOf(p.useDefault) || /^\d+$/.test(value())
      ? null
      : { kind: 'hours', message: 'Enter whole hours' },
  );
});

/** The chrome composed over one draft; `touched` pre-touches every field, `formError` puts a line on the form root. */
@Component({
  selector: 'fleet-story-form-chrome',
  imports: [
    FormField,
    KitForm,
    KitField,
    KitTextInput,
    KitNumberInput,
    KitOrDefault,
  ],
  templateUrl: './kit-form-chrome.form-story.html',
})
export class FormChromeHost {
  readonly slug = input('');
  readonly hours = input('12x');
  readonly useDefault = input(false);
  readonly touched = input(false);
  readonly submitting = input(false);
  readonly saveDisabled = input(false);
  readonly formError = input<string | null>(null);

  readonly f = form(
    signal<Draft>({ slug: '', useDefault: false, hours: '' }),
    rules,
  );

  constructor() {
    queueMicrotask(() => this.seed());
  }

  private seed(): void {
    this.f.slug().value.set(this.slug());
    this.f.hours().value.set(this.hours());
    this.f.useDefault().value.set(this.useDefault());
    if (this.touched() || this.formError() !== null) this.f().markAsTouched();
    if (this.touched()) {
      this.f.slug().markAsTouched();
      this.f.hours().markAsTouched();
    }
    if (this.submitting())
      void submit(this.f, () => new Promise<undefined>(() => undefined));
    else if (this.formError() !== null) {
      void submit(this.f, async () => [
        { kind: 'hub', fieldTree: this.f, message: this.formError()! },
      ]);
    }
  }

  protected submit(): void {
    void submit(this.f, async () => undefined);
  }
}

/** What a stand-in save answers: ok, or a refusal naming the slug and the form line (the kit cannot import `core/forms`). */
export type StandInOutcome =
  { ok: true } | { ok: false; slug: string | null; form: string | null };

/** Submits like a container would: touches every field, runs `save` only when valid, focuses the first field in error. */
export async function submitDraft<D extends { slug: string }>(
  f: FieldTree<D>,
  save: () => Promise<StandInOutcome>,
): Promise<boolean> {
  const ok = await submit(f, async () => {
    const outcome = await save();
    if (outcome.ok) return undefined;
    const errors: ValidationError.WithOptionalFieldTree[] = [];
    if (outcome.slug !== null)
      errors.push({
        kind: 'hub',
        fieldTree: (f as unknown as Record<'slug', FieldTree<unknown>>).slug,
        message: outcome.slug,
      });
    if (outcome.form !== null)
      errors.push({ kind: 'hub', fieldTree: f, message: outcome.form });
    return errors;
  });
  if (!ok)
    f()
      .errorSummary()
      .find((error) => error.fieldTree !== f)
      ?.fieldTree()
      .focusBoundControl();
  return ok;
}

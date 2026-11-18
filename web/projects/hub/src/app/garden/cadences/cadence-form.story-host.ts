import { Component, input, signal } from '@angular/core';
import { form, submit } from '@angular/forms/signals';

import { type CadenceDraft, EMPTY_CADENCE_DRAFT } from '../core/cadence.domain';
import { cadenceCreateRules, cadenceEditRules } from './cadence.form';
import { FleetCadenceForm } from './cadence-form';

/**
 * The cadence form over a `form()` this host owns, as its containers own one. `draft` seeds the fields; `touched`
 * touches every field so rule failures show; `slugError` and `formError` land a hub refusal on the slug and the form
 * line.
 */
@Component({
  selector: 'app-story-cadence-form',
  imports: [FleetCadenceForm],
  templateUrl: './cadence-form.form-story.html',
})
export class CadenceFormStoryHost {
  readonly creating = input(true);
  readonly draft = input<Partial<CadenceDraft>>({});
  readonly touched = input(false);
  readonly slugError = input<string | null>(null);
  readonly formError = input<string | null>(null);

  private readonly model = signal<CadenceDraft>(EMPTY_CADENCE_DRAFT);
  protected readonly createForm = form(this.model, cadenceCreateRules);
  protected readonly editForm = form(this.model, cadenceEditRules);

  constructor() {
    queueMicrotask(() => this.seed());
  }

  private seed(): void {
    this.model.set({ ...EMPTY_CADENCE_DRAFT, ...this.draft() });
    const f = this.creating() ? this.createForm : this.editForm;
    if (this.touched()) f().markAsTouched();
    const slugError = this.slugError();
    const formError = this.formError();
    if (slugError !== null || formError !== null) {
      void submit(f, async () => [
        ...(slugError === null ? [] : [{ kind: 'hub', fieldTree: f.slug, message: slugError }]),
        ...(formError === null ? [] : [{ kind: 'hub', fieldTree: f, message: formError }]),
      ]);
    }
  }

  protected onSave(): void {
    const f = this.creating() ? this.createForm : this.editForm;
    void submit(f, async () => undefined);
  }
}

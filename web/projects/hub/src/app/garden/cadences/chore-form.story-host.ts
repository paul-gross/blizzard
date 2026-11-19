import { Component, input, signal } from '@angular/core';
import { form, submit } from '@angular/forms/signals';

import type { Urgency } from 'fleet';

import { type ChoreDraft, EMPTY_CHORE_DRAFT } from '../core/cadence.domain';
import { choreAddRules, choreEditRules } from './chore.form';
import { FleetChoreForm } from './chore-form';

/**
 * The chore form over a `form()` this host owns, as its container owns one. `draft` seeds the fields; `touched`
 * touches every field so rule failures show; `keyError` and `formError` land a hub refusal on the key and the form
 * line.
 */
@Component({
  selector: 'app-story-chore-form',
  imports: [FleetChoreForm],
  templateUrl: './chore-form.form-story.html',
})
export class ChoreFormStoryHost {
  readonly adding = input(true);
  readonly draft = input<Partial<ChoreDraft>>({});
  readonly touched = input(false);
  readonly keyError = input<string | null>(null);
  readonly formError = input<string | null>(null);
  readonly urgencies = input<readonly Urgency[]>([]);
  readonly heldMode = input<string | null>(null);

  private readonly model = signal<ChoreDraft>(EMPTY_CHORE_DRAFT);
  protected readonly addForm = form(this.model, choreAddRules);
  protected readonly editForm = form(this.model, choreEditRules);

  constructor() {
    queueMicrotask(() => this.seed());
  }

  private seed(): void {
    this.model.set({ ...EMPTY_CHORE_DRAFT, ...this.draft() });
    const f = this.adding() ? this.addForm : this.editForm;
    if (this.touched()) f().markAsTouched();
    const keyError = this.keyError();
    const formError = this.formError();
    if (keyError !== null || formError !== null) {
      void submit(f, async () => [
        ...(keyError === null ? [] : [{ kind: 'hub', fieldTree: f.key, message: keyError }]),
        ...(formError === null ? [] : [{ kind: 'hub', fieldTree: f, message: formError }]),
      ]);
    }
  }

  protected onSave(): void {
    const f = this.adding() ? this.addForm : this.editForm;
    void submit(f, async () => undefined);
  }
}

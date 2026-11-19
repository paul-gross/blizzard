import { Component, input, signal } from '@angular/core';
import { form, submit } from '@angular/forms/signals';

import type { UrgencyView } from 'fleet';

import { EMPTY_ROUTINE_CREATE_DRAFT, type RoutineCreateDraft } from '../core/routine.domain';
import { RoutineCreateForm } from './routine-create-form';
import { routineCreateRules } from './routine.form';

/**
 * The routine create form over a `form()` this host owns, as its container owns one. `draft` seeds the fields;
 * `touched` touches every field so rule failures show; `scopeError` and `formError` land a hub refusal on the scope
 * and the form line.
 */
@Component({
  selector: 'app-story-routine-create-form',
  imports: [RoutineCreateForm],
  templateUrl: './routine-create-form.form-story.html',
})
export class RoutineCreateFormStoryHost {
  readonly draft = input<Partial<RoutineCreateDraft>>({});
  readonly touched = input(false);
  readonly scopeError = input<string | null>(null);
  readonly formError = input<string | null>(null);
  readonly urgencies = input<readonly UrgencyView[]>([]);

  private readonly model = signal<RoutineCreateDraft>(EMPTY_ROUTINE_CREATE_DRAFT);
  protected readonly createForm = form(this.model, routineCreateRules);

  constructor() {
    queueMicrotask(() => this.seed());
  }

  private seed(): void {
    this.model.set({ ...EMPTY_ROUTINE_CREATE_DRAFT, ...this.draft() });
    const f = this.createForm;
    if (this.touched()) f().markAsTouched();
    const scopeError = this.scopeError();
    const formError = this.formError();
    if (scopeError !== null || formError !== null) {
      void submit(f, async () => [
        ...(scopeError === null ? [] : [{ kind: 'hub', fieldTree: f.scope, message: scopeError }]),
        ...(formError === null ? [] : [{ kind: 'hub', fieldTree: f, message: formError }]),
      ]);
    }
  }

  protected onSave(): void {
    void submit(this.createForm, async () => undefined);
  }
}

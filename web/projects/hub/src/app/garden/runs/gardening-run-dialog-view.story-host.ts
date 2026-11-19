import { Component, input, signal } from '@angular/core';
import { form, submit } from '@angular/forms/signals';

import type { KitAsyncStateValue, RoutineBaselineView, ScopeView, UrgencyView } from 'fleet';

import { EMPTY_RUN_DRAFT, type RunDraft } from '../core/run.domain';
import { GardeningRunDialogView } from './gardening-run-dialog-view';
import { runRules } from './run.form';

/**
 * The run dialog view over a `form()` this host owns, as its container owns one. `draft` seeds the fields; `formError`
 * and `scopeError` land a hub refusal on the form line and on the scope.
 */
@Component({
  selector: 'app-story-run-dialog',
  imports: [GardeningRunDialogView],
  templateUrl: './gardening-run-dialog-view.form-story.html',
})
export class RunDialogStoryHost {
  readonly draft = input<Partial<RunDraft>>({});
  readonly scopes = input<readonly ScopeView[]>([]);
  readonly swept = input<ReadonlySet<string>>(new Set());
  readonly baseline = input<RoutineBaselineView | undefined>(undefined);
  readonly state = input<KitAsyncStateValue>('ready');
  readonly submitting = input(false);
  readonly urgencies = input<readonly UrgencyView[]>([]);
  readonly formError = input<string | null>(null);
  readonly scopeError = input<string | null>(null);

  private readonly model = signal<RunDraft>(EMPTY_RUN_DRAFT);
  protected readonly runForm = form(
    this.model,
    runRules(
      () => new Set(this.scopes().map((scope) => scope.slug)),
      () => this.swept(),
    ),
  );

  constructor() {
    queueMicrotask(() => {
      this.model.set({ ...EMPTY_RUN_DRAFT, ...this.draft() });
      const scopeError = this.scopeError();
      const formError = this.formError();
      if (scopeError !== null || formError !== null) {
        void submit(this.runForm, async () => [
          ...(scopeError === null ? [] : [{ kind: 'hub', fieldTree: this.runForm.scope, message: scopeError }]),
          ...(formError === null ? [] : [{ kind: 'hub', fieldTree: this.runForm, message: formError }]),
        ]);
      }
    });
  }
}

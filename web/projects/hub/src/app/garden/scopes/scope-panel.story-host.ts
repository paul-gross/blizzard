import { Component, input, signal } from '@angular/core';
import { form, submit } from '@angular/forms/signals';

import type { KitAsyncStateValue } from 'fleet';

import { type ScopeDescriptionDraft, scopeDescriptionDraft } from '../core/scope.domain';
import { FleetScopePanel, type ScopePanelVm } from './scope-panel';
import { SCOPE_DESCRIPTION_RULES } from './scope.form';

/**
 * The scope panel over a description `form()` this host owns, as the scope detail container owns one: Edit opens the
 * draft on the scope's description, Cancel closes it, and Set validates and emits nothing further. `editing` opens the
 * editor from the start; `fieldError` and `formError` land a hub refusal on the description and on the form line.
 */
@Component({
  selector: 'app-story-scope-panel',
  imports: [FleetScopePanel],
  templateUrl: './scope-panel.form-story.html',
})
export class ScopePanelStoryHost {
  readonly vm = input<ScopePanelVm | null>(null);
  readonly state = input<KitAsyncStateValue>('ready');
  readonly relatedRoutinesState = input<KitAsyncStateValue>('ready');
  readonly project = input<string | null>(null);
  readonly sameSlugIn = input<readonly string[]>([]);
  readonly canEdit = input(true);
  readonly editPending = input(false);
  readonly editing = input(false);
  readonly fieldError = input<string | null>(null);
  readonly formError = input<string | null>(null);

  protected readonly isEditing = signal(false);
  private readonly draft = signal<ScopeDescriptionDraft>({ description: '' });
  protected readonly descriptionForm = form(this.draft, SCOPE_DESCRIPTION_RULES);

  constructor() {
    queueMicrotask(() => {
      if (this.editing()) this.start();
      const fieldError = this.fieldError();
      const formError = this.formError();
      if (fieldError !== null || formError !== null) {
        void submit(this.descriptionForm, async () => [
          ...(fieldError === null ? [] : [{ kind: 'hub', fieldTree: this.descriptionForm.description, message: fieldError }]),
          ...(formError === null ? [] : [{ kind: 'hub', fieldTree: this.descriptionForm, message: formError }]),
        ]);
      }
    });
  }

  protected start(): void {
    const vm = this.vm();
    if (vm !== null) this.draft.set(scopeDescriptionDraft(vm));
    this.isEditing.set(true);
  }

  protected save(): void {
    void submit(this.descriptionForm, async () => undefined);
  }
}

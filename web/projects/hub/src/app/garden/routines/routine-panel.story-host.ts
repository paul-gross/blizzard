import { Component, input, signal } from '@angular/core';
import { form, submit } from '@angular/forms/signals';

import type { Urgency } from 'fleet/core';

import { EMPTY_ROUTINE_EDIT_DRAFT, type RoutineEditDraft } from '../core/routine.domain';
import { type RoutineEditField, type RoutinePanelVm, FleetRoutinePanel } from './routine-panel';
import { routineEditRules } from './routine.form';

/**
 * The routine panel with an inline field open, over an edit `form()` this host owns as the detail container does.
 * `draft` seeds the fields; `fieldError` lands a hub refusal on the open field and `formError` on the form line, as a
 * revision conflict does.
 */
@Component({
  selector: 'app-story-routine-panel-editing',
  imports: [FleetRoutinePanel],
  templateUrl: './routine-panel.form-story.html',
})
export class RoutinePanelEditingStoryHost {
  readonly vm = input.required<RoutinePanelVm>();
  readonly field = input<RoutineEditField>('graph');
  readonly draft = input<Partial<RoutineEditDraft>>({});
  readonly fieldError = input<string | null>(null);
  readonly formError = input<string | null>(null);
  readonly urgencies = input<readonly Urgency[]>([]);

  private readonly model = signal<RoutineEditDraft>(EMPTY_ROUTINE_EDIT_DRAFT);
  protected readonly tree = form(this.model, routineEditRules);

  constructor() {
    queueMicrotask(() => this.seed());
  }

  private seed(): void {
    this.model.set({ ...EMPTY_ROUTINE_EDIT_DRAFT, graph: this.vm().record.graphName, ...this.draft() });
    const fieldError = this.fieldError();
    const formError = this.formError();
    if (fieldError === null && formError === null) return;
    const target = this.field() === 'graph' ? this.tree.graph : this.tree.urgency;
    void submit(this.tree, async () => [
      ...(fieldError === null ? [] : [{ kind: 'hub', fieldTree: target, message: fieldError }]),
      ...(formError === null ? [] : [{ kind: 'hub', fieldTree: this.tree, message: formError }]),
    ]);
  }
}

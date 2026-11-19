import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';
import { apply, form } from '@angular/forms/signals';

import { createRefusalState, submitForm } from '../../core/forms/submit';
import { type AwaitingDecision, type AwaitingHumanFormActions, type DecisionNoteDraft, EMPTY_DECISION_NOTE_DRAFT } from './awaiting-human.domain';
import { ChunkDecisionFormView } from './chunk-decision-form-view';

/**
 * One writable open gate's note form: it owns the note's draft, its refusal state, and its submit. A picked choice
 * submits the note with it, through the app's {@link AwaitingHumanFormActions}; a refusal keeps the typed note.
 */
@Component({
  selector: 'fleet-chunk-decision-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkDecisionFormView],
  templateUrl: './chunk-decision-form.html',
})
export class ChunkDecisionForm {
  readonly chunkId = input.required<string>();
  readonly decision = input.required<AwaitingDecision>();
  readonly actions = input.required<AwaitingHumanFormActions>();

  private readonly draft = signal<DecisionNoteDraft>(EMPTY_DECISION_NOTE_DRAFT);
  private readonly refusal = createRefusalState<DecisionNoteDraft>();
  protected readonly noteForm = form(this.draft, (p) => {
    apply(p, this.refusal.schema);
  });

  protected async onChoose(choice: string): Promise<void> {
    await submitForm(
      this.noteForm,
      (draft) => this.actions().resolve(this.chunkId(), this.decision().decision_id, choice, draft),
      { refusalState: this.refusal },
    );
  }
}

import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';
import { apply, form } from '@angular/forms/signals';

import { createRefusalState, submitForm } from '../../core/forms/submit';
import type { SaveOutcome } from '../../core/forms/outcome';
import { type AnswerDraft, type AwaitingHumanFormActions, type AwaitingQuestion, EMPTY_ANSWER_DRAFT } from './awaiting-human.domain';
import { answerRules } from './awaiting-human.form';
import { ChunkQuestionFormView } from './chunk-question-form-view';

/** A refusal of a picked option, all on the form line: the typed field never owns what the option sent. */
function onFormLine(outcome: SaveOutcome<AnswerDraft>): SaveOutcome<AnswerDraft> {
  if (outcome.ok) return outcome;
  const lines = [...Object.values(outcome.fields), ...(outcome.form === null ? [] : [outcome.form])] as string[];
  return { ok: false, fields: {}, form: lines.length > 0 ? lines.join('; ') : null };
}

/**
 * One writable open question's answer form: it owns the question's draft, its rules, its refusal state, and its submit,
 * so every question's typed text, touched state, refusal, and pending state stand apart. Saves through the app's
 * {@link AwaitingHumanFormActions}; a refusal keeps the typed answer.
 */
@Component({
  selector: 'fleet-chunk-question-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkQuestionFormView],
  templateUrl: './chunk-question-form.html',
})
export class ChunkQuestionForm {
  readonly chunkId = input.required<string>();
  readonly question = input.required<AwaitingQuestion>();
  readonly actions = input.required<AwaitingHumanFormActions>();

  private readonly draft = signal<AnswerDraft>(EMPTY_ANSWER_DRAFT);
  private readonly refusal = createRefusalState<AnswerDraft>();
  protected readonly answerForm = form(this.draft, (p) => {
    apply(p, answerRules);
    apply(p, this.refusal.schema);
  });

  protected async onAnswer(): Promise<void> {
    await submitForm(this.answerForm, (draft) => this.save(draft), { refusalState: this.refusal });
  }

  /** Submits the picked option, not the typed text, so the typed field's own rule does not hold it back — nor, when
   * the operator never touched it, does the submit leave it showing that rule. */
  protected async onPick(option: string): Promise<void> {
    const typed = this.answerForm.answer();
    const touched = typed.touched();
    await submitForm(this.answerForm, async () => onFormLine(await this.save({ answer: option })), {
      refusalState: this.refusal,
      ignoreLocalErrors: true,
    });
    if (!touched) typed.reset();
  }

  private save(draft: AnswerDraft): Promise<SaveOutcome<AnswerDraft>> {
    return this.actions().answer(this.chunkId(), this.question().question_id, draft);
  }
}

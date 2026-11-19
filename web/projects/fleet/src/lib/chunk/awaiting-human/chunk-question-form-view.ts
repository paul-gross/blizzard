import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';

import { KitButton } from '../../kit/kit-button';
import { KitChip } from '../../kit/kit-chips';
import { KitErrorLine } from '../../kit/kit-error-line';
import { KitField } from '../../kit/kit-field';
import { KitTextInput } from '../../kit/kit-text-input';
import { KitToolbar, KitToolbarWidget } from '../../kit/kit-toolbar';
import type { AnswerDraft, AwaitingQuestion } from './awaiting-human.domain';

/**
 * One open question's answer controls: the offered options as a toolbar of actions, the typed answer's field, the
 * Answer action, and the form line for what the hub refused that no field owns. Presentational: it renders the field
 * tree it is handed and emits which action was taken; {@link ChunkQuestionForm} owns the draft and the save.
 */
@Component({
  selector: 'fleet-chunk-question-form-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitButton, KitChip, KitErrorLine, KitField, KitTextInput, KitToolbar, KitToolbarWidget],
  templateUrl: './chunk-question-form-view.html',
  styleUrl: './chunk-question-form-view.css',
})
export class ChunkQuestionFormView {
  readonly question = input.required<AwaitingQuestion>();
  readonly form = input.required<FieldTree<AnswerDraft>>();

  /** Answer pressed: submit the typed answer. */
  readonly answer = output<void>();

  /** An offered option picked: submit it in place of the typed answer. */
  readonly pick = output<string>();

  /** Whether this question's save is in flight — disables only its own controls. */
  protected readonly submitting = computed(() => this.form()().submitting());

  /** What the hub refused that no field owns. */
  protected readonly formErrors = computed(() =>
    this.form()()
      .errors()
      .map((error) => error.message || 'Answer failed.'),
  );
}

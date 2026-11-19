import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';

import { KitChip } from '../../kit/kit-chips';
import { KitErrorLine } from '../../kit/kit-error-line';
import { KitField } from '../../kit/kit-field';
import { KitTextInput } from '../../kit/kit-text-input';
import { KitToolbar, KitToolbarWidget } from '../../kit/kit-toolbar';
import type { AwaitingDecision, DecisionNoteDraft } from './awaiting-human.domain';

/**
 * One open gate's resolve controls: the optional note's field, the gate's choices as a toolbar of actions, and the form
 * line for what the hub refused that no field owns. Presentational: it renders the field tree it is handed and emits
 * the picked choice; {@link ChunkDecisionForm} owns the draft and the save.
 */
@Component({
  selector: 'fleet-chunk-decision-form-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitChip, KitErrorLine, KitField, KitTextInput, KitToolbar, KitToolbarWidget],
  templateUrl: './chunk-decision-form-view.html',
  styleUrl: './chunk-decision-form-view.css',
})
export class ChunkDecisionFormView {
  readonly decision = input.required<AwaitingDecision>();
  readonly form = input.required<FieldTree<DecisionNoteDraft>>();

  /** A choice picked: resolve the gate with it and the note. */
  readonly choose = output<string>();

  /** Whether this gate's save is in flight — disables its choices. */
  protected readonly submitting = computed(() => this.form()().submitting());

  /** What the hub refused that no field owns. */
  protected readonly formErrors = computed(() =>
    this.form()()
      .errors()
      .map((error) => error.message || 'Resolve failed.'),
  );
}

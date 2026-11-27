import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import { KitField, KitForm, KitTextInput } from 'fleet/kit';
import { KitDialog } from 'fleet/kit/dialog';

import { type FindingTriageVerb } from '../../core/finding-triage-verb.domain';
import { type FindingTriageDraft } from './finding-triage.domain';

/** Every verb's human-readable dialog heading label. */
const VERB_LABELS: Record<FindingTriageVerb, string> = {
  resolve: 'Resolve',
  'confirm-gone': 'Confirm gone',
  'wont-fix': "Won't fix",
  'not-a-finding': 'Not a finding',
  supersede: 'Supersede',
  reopen: 'Reopen',
};

/** What each verb does to the finding, shown above the note field. */
const VERB_BLURBS: Record<FindingTriageVerb, string> = {
  resolve: 'The work that answers this finding has landed. It leaves the live bucket as resolved.',
  'confirm-gone':
    'A run reported this finding no longer reproduces, and you are confirming that by hand. It leaves the live bucket as gone-confirmed.',
  'wont-fix':
    "The finding stands, but it doesn't merit acting on. Nothing about the ground changed — this is a judgment call, and it withdraws the finding.",
  'not-a-finding': 'This should not have been recorded as a finding at all. It withdraws the finding.',
  supersede:
    'Another finding absorbs this one and carries it forward. It withdraws the finding, pointing at the absorbing id named below.',
  reopen: 'Puts this finding back in the live bucket, undoing whichever exit or gone fact was newest.',
};

/**
 * The findings triage dialog's presentational view — a `KitForm` over the container's `form()`: a required note
 * every verb takes plus, for `supersede` alone, a field naming the absorbing finding. The verb is fixed for the
 * dialog's whole lifetime, so the extra field is a template branch on {@link verb}. No query or client
 * dependency (`bzh:frontend-container-presentational`): the container owns the draft and answers {@link save}.
 *
 * Opens on a short statement of what the chosen verb does ({@link VERB_BLURBS}).
 */
@Component({
  selector: 'app-gardening-finding-triage-dialog-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitDialog, KitField, KitForm, KitTextInput],
  templateUrl: './gardening-finding-triage-dialog-view.html',
  styleUrl: './gardening-finding-triage-dialog-view.css',
})
export class GardeningFindingTriageDialogView {
  readonly verb = input.required<FindingTriageVerb>();
  readonly form = input.required<FieldTree<FindingTriageDraft>>();

  readonly closed = output<void>();
  readonly save = output<void>();

  protected readonly heading = computed(() => `${VERB_LABELS[this.verb()]} finding`);

  /** {@link VERB_BLURBS}'s lookup for the verb this dialog was opened under. */
  protected readonly blurb = computed(() => VERB_BLURBS[this.verb()]);

  /** Escape, a backdrop click, and Cancel all route through one close — gated here so a triage action in flight
   * cannot be torn down before it lands. */
  protected onClosed(): void {
    if (this.form()().submitting()) return;
    this.closed.emit();
  }
}

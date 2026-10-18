import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { KitButton, KitDialog, KitTextInput } from 'fleet';
import { type FindingTriageVerb } from '../core/finding-list';

/** What the view asks the container to submit — `supersededBy` rides only when
 * the dialog's fixed {@link GardeningFindingTriageDialogView.verb} is
 * `'supersede'`. */
export interface FindingTriageSubmission {
  readonly note: string;
  readonly supersededBy?: string;
}

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
 * The findings triage dialog's presentational view
 * — a required note field every verb takes plus, for `supersede` alone, a second
 * required field naming the absorbing finding,
 * `gardening-proposal-accept-dialog-view.ts`'s own conditional-extra-field
 * shape. Here the branch is on the {@link verb} input rather than a local mode
 * signal — the verb is fixed for this dialog's whole lifetime, chosen before it
 * ever opened. No query or client dependency: the container injects the
 * matching mutation and maps its async state into `submitting()`/`submitError()`
 * (`bzh:frontend-container-presentational`).
 *
 * Opens on a short statement of what the chosen verb does ({@link VERB_BLURBS}).
 * Owns the note/`supersededBy` fields as local signals.
 */
@Component({
  selector: 'app-gardening-finding-triage-dialog-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitDialog, KitTextInput],
  templateUrl: './gardening-finding-triage-dialog-view.html',
  styleUrl: './gardening-finding-triage-dialog-view.css',
})
export class GardeningFindingTriageDialogView {
  readonly verb = input.required<FindingTriageVerb>();

  readonly submitting = input(false);
  readonly submitError = input<string | null>(null);

  readonly closed = output<void>();
  readonly submitted = output<FindingTriageSubmission>();

  protected readonly note = signal('');
  protected readonly supersededBy = signal('');

  protected readonly heading = computed(() => `${VERB_LABELS[this.verb()]} finding`);

  /** {@link VERB_BLURBS}'s lookup for the verb this dialog was opened under. */
  protected readonly blurb = computed(() => VERB_BLURBS[this.verb()]);

  protected readonly canSubmit = computed(() => {
    if (this.submitting()) return false;
    if (this.note().trim().length === 0) return false;
    return this.verb() !== 'supersede' || this.supersededBy().trim().length > 0;
  });

  protected onSubmitClick(): void {
    if (!this.canSubmit()) return;
    const note = this.note().trim();
    if (this.verb() === 'supersede') {
      this.submitted.emit({ note, supersededBy: this.supersededBy().trim() });
      return;
    }
    this.submitted.emit({ note });
  }

  /** Escape, a backdrop click, and Cancel all route through `KitDialog`'s one
   * `(closed)` output — gated here so a triage action in flight cannot be torn
   * down before it lands. */
  protected onClosed(): void {
    if (this.submitting()) return;
    this.closed.emit();
  }
}

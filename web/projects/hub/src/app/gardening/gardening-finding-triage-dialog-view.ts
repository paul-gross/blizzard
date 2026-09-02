import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { KitButton, KitDialog, KitTextInput, type FindingTriageVerb } from 'fleet';

/** What the view asks the container to submit — `supersededBy` rides only when
 * the dialog's fixed {@link GardeningFindingTriageDialogView.verb} is
 * `'supersede'`. */
export interface FindingTriageSubmission {
  readonly note: string;
  readonly supersededBy?: string;
}

/** Escapes a value for safe interpolation inside a double-quoted shell argument —
 * backslashes first, then double quotes — so {@link GardeningFindingTriageDialogView.cliVerb}'s
 * `--note "..."` mirror stays a command that actually runs if copy-pasted even when
 * the note itself contains a `"` (F11). */
function shellDoubleQuoted(value: string): string {
  return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
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

/**
 * The findings triage bulk-action dialog's presentational view (Decisions 1, 5, 7)
 * — a required note field every verb takes plus, for `supersede` alone, a second
 * required field naming the absorbing finding,
 * `gardening-proposal-accept-dialog-view.ts`'s own conditional-extra-field
 * shape. Here the branch is on the {@link verb} input rather than a local mode
 * signal — the verb is fixed for this dialog's whole lifetime, chosen before it
 * ever opened. No query or client dependency: the container injects the
 * matching mutation and maps its async state into `submitting()`/`submitError()`
 * (`bzh:frontend-container-presentational`).
 *
 * The CLI-verb mirror line (D1) is built from the real CLI
 * (`src/blizzard/hub/cli/finding.py`) — every finding id space-joined, matching
 * the CLI's own `nargs=-1` positional-args shape, with `--note "..."` appended
 * only once the note is non-blank (`gardening-proposal-pass-dialog-view.ts`'s
 * own conditional-flag shape), and `supersede` alone also carrying `--by`.
 *
 * Owns the note/`supersededBy` fields as local signals — the host page renders
 * this component (and its container) with `@if`, tearing it down between opens,
 * so a stale value never survives to a later open.
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
  readonly findingIds = input.required<readonly string[]>();

  readonly submitting = input(false);
  readonly submitError = input<string | null>(null);

  readonly closed = output<void>();
  readonly submitted = output<FindingTriageSubmission>();

  protected readonly note = signal('');
  protected readonly supersededBy = signal('');

  protected readonly heading = computed(() => {
    const count = this.findingIds().length;
    return `${VERB_LABELS[this.verb()]} ${count} ${count === 1 ? 'finding' : 'findings'}`;
  });

  protected readonly canSubmit = computed(() => {
    if (this.submitting()) return false;
    if (this.note().trim().length === 0) return false;
    return this.verb() !== 'supersede' || this.supersededBy().trim().length > 0;
  });

  protected readonly cliVerb = computed(() => {
    const verb = this.verb();
    const ids = this.findingIds().join(' ');
    const parts = [`blizzard hub finding ${verb} ${ids}`];
    if (verb === 'supersede') parts.push(`--by ${this.supersededBy().trim()}`);
    const note = this.note().trim();
    if (note) parts.push(`--note ${shellDoubleQuoted(note)}`);
    return parts.join(' ');
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

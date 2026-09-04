import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal } from '@angular/core';
import { KitButton, KitDialog, KitOption, KitPanel, KitTextInput } from 'fleet';

/** What the view asks the container to submit — `mintWorkItem` mirrors the CLI's
 * `--no-work-item` (inverted), `body`/`reason` ride only when they carry something,
 * never as an explicit empty string. */
export interface AcceptSubmission {
  readonly mintWorkItem: boolean;
  readonly body?: string;
  readonly reason?: string;
}

/** The docket's two closing choices for Accept — minting is the default; declining
 * is available and, per Decision 5, deliberately the more effortful path: it gates
 * submission on its own required reason where minting gates on nothing. */
type AcceptMode = 'mint' | 'decline';

/**
 * The Accept dialog's presentational view (Decision 5) —
 * `gardening-run-dialog-view.ts`'s own scaffold: the mint/decline choice, the
 * prefilled editable body (mint only), and an optional reason either way. No query
 * or client dependency: the container injects the mutation and maps its async state
 * into `submitting()`/`submitError()` (`bzh:frontend-container-presentational`).
 *
 * One `reason` signal backs both modes' text field — mint's optional reason and
 * decline's required one are the same conceptual field, never two, so submission
 * gating branches on {@link mode} alone rather than on which signal carries the text.
 *
 * Copy states that acceptance neither promotes the minted item nor changes any
 * finding's state — the surface's own answer to the two things acceptance does not
 * do.
 */
@Component({
  selector: 'app-gardening-proposal-accept-dialog-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitDialog, KitOption, KitPanel, KitTextInput],
  templateUrl: './gardening-proposal-accept-dialog-view.html',
  styleUrl: './gardening-proposal-accept-dialog-view.css',
})
export class GardeningProposalAcceptDialogView {
  readonly proposalTitle = input.required<string>();
  readonly proposalBody = input.required<string>();

  readonly submitting = input(false);
  readonly submitError = input<string | null>(null);

  readonly closed = output<void>();
  readonly submitted = output<AcceptSubmission>();

  protected readonly mode = signal<AcceptMode>('mint');
  protected readonly body = signal('');
  protected readonly reason = signal('');

  constructor() {
    // Prefills the editable body with the proposal's own — the container passes an
    // already-resolved value (the selected proposal's own record, already in hand
    // from the docket list read), so this fires once and never re-clobbers an
    // operator's edit.
    effect(() => this.body.set(this.proposalBody()));
  }

  protected readonly canSubmit = computed(() => {
    if (this.submitting()) return false;
    return this.mode() === 'mint' || this.reason().trim().length > 0;
  });

  protected onSubmitClick(): void {
    if (!this.canSubmit()) return;
    const reason = this.reason().trim();
    if (this.mode() === 'decline') {
      this.submitted.emit({ mintWorkItem: false, reason });
      return;
    }
    const body = this.body().trim();
    this.submitted.emit({ mintWorkItem: true, ...(body ? { body } : {}), ...(reason ? { reason } : {}) });
  }

  /** Escape, a backdrop click, and Cancel all route through `KitDialog`'s one
   * `(closed)` output — gated here so an accept in flight cannot be torn down
   * before it lands. */
  protected onClosed(): void {
    if (this.submitting()) return;
    this.closed.emit();
  }
}

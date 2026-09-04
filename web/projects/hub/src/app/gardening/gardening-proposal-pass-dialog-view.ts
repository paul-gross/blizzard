import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { KitButton, KitDialog, KitTextInput } from 'fleet';

/**
 * The Pass dialog's presentational view (Decision 5) — one required
 * reason field and nothing else, `gardening-run-dialog-view.ts`'s own scaffold. No
 * query or client dependency: the container injects the mutation and maps its async
 * state into `submitting()`/`submitError()` (`bzh:frontend-container-presentational`).
 *
 * Owns the reason field as a local signal — the host page renders this component
 * (and its container) with `@if`, tearing it down between opens, so a stale value
 * never survives to a later open.
 */
@Component({
  selector: 'app-gardening-proposal-pass-dialog-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitDialog, KitTextInput],
  templateUrl: './gardening-proposal-pass-dialog-view.html',
  styleUrl: './gardening-proposal-pass-dialog-view.css',
})
export class GardeningProposalPassDialogView {
  readonly proposalTitle = input.required<string>();

  readonly submitting = input(false);
  readonly submitError = input<string | null>(null);

  readonly closed = output<void>();
  readonly submitted = output<string>();

  protected readonly reason = signal('');

  protected readonly canSubmit = computed(() => this.reason().trim().length > 0 && !this.submitting());

  protected onSubmitClick(): void {
    if (!this.canSubmit()) return;
    this.submitted.emit(this.reason().trim());
  }

  /** Escape, a backdrop click, and Cancel all route through `KitDialog`'s one
   * `(closed)` output — gated here so a pass in flight cannot be torn down before it
   * lands. */
  protected onClosed(): void {
    if (this.submitting()) return;
    this.closed.emit();
  }
}

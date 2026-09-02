import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { errorMessage, injectPassGardenProposalMutation } from 'fleet';

import { GardeningProposalPassDialogView } from './gardening-proposal-pass-dialog-view';

/**
 * The garden proposal docket's Pass dialog container (Decisions 5, 7) —
 * `blizzard hub garden-proposal pass <id> --reason <text>`'s own UI: a single
 * required-reason field, submitted through `injectPassGardenProposalMutation`
 * (`bzh:generated-client`). Closure is terminal, so a 409 (a raced second close) is a
 * real outcome, not an impossible one — it surfaces through this container's own
 * `submitError`, exactly the mutation's `errorMessage` fallback shape every other
 * mutation in this app already answers with.
 *
 * The host page mounts this with `@if` around the passing proposal
 * (`gardening-proposals-page.ts`'s own dialog-open signal), so a fresh instance — and
 * a fresh view, with its own fresh reason field — exists for every open.
 */
@Component({
  selector: 'app-gardening-proposal-pass-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GardeningProposalPassDialogView],
  templateUrl: './gardening-proposal-pass-dialog.html',
})
export class GardeningProposalPassDialog {
  readonly proposalId = input.required<string>();
  readonly proposalTitle = input.required<string>();

  readonly closed = output<void>();

  private readonly passMutation = injectPassGardenProposalMutation();

  protected readonly submitting = computed(() => this.passMutation.isPending());
  protected readonly submitError = signal<string | null>(null);

  protected onSubmit(reason: string): void {
    this.submitError.set(null);
    this.passMutation.mutate(
      { proposalId: this.proposalId(), reason },
      {
        onSuccess: () => this.closed.emit(),
        onError: (error: unknown) => this.submitError.set(errorMessage(error, 'Pass failed.')),
      },
    );
  }
}

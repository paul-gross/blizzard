import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { errorMessage, injectAcceptGardenProposalMutation } from 'fleet';

import { GardeningProposalAcceptDialogView, type AcceptSubmission } from './gardening-proposal-accept-dialog-view';

/**
 * The garden proposal docket's Accept dialog container (Decisions 5, 6,
 * 7) — `blizzard hub garden-proposal accept <id> [--reason] [--body-file] [--no-work-
 * item]`'s own UI, submitted through `injectAcceptGardenProposalMutation`
 * (`bzh:generated-client`). Minting is the default and submits in one click;
 * declining to mint is the more effortful path (Decision 5), gated in the view on its
 * own required reason. Closure is terminal, so a 409 (a raced second close) surfaces
 * through this container's own `submitError`.
 *
 * The host page mounts this with `@if` around the accepting proposal, so a fresh
 * instance — and a fresh view — exists for every open.
 */
@Component({
  selector: 'app-gardening-proposal-accept-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GardeningProposalAcceptDialogView],
  templateUrl: './gardening-proposal-accept-dialog.html',
})
export class GardeningProposalAcceptDialog {
  readonly proposalId = input.required<string>();
  readonly proposalTitle = input.required<string>();
  readonly proposalBody = input.required<string>();

  readonly closed = output<void>();

  private readonly acceptMutation = injectAcceptGardenProposalMutation();

  protected readonly submitting = computed(() => this.acceptMutation.isPending());
  protected readonly submitError = signal<string | null>(null);

  protected onSubmit(submission: AcceptSubmission): void {
    this.submitError.set(null);
    this.acceptMutation.mutate(
      { proposalId: this.proposalId(), ...submission },
      {
        onSuccess: () => this.closed.emit(),
        onError: (error: unknown) => this.submitError.set(errorMessage(error, 'Accept failed.')),
      },
    );
  }
}

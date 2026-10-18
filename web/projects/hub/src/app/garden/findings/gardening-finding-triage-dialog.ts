import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { errorMessage } from 'fleet';
import { injectConfirmGoneFindingsMutation, injectNotAFindingFindingsMutation, injectReopenFindingsMutation, injectResolveFindingsMutation, injectSupersedeFindingsMutation, injectWontFixFindingsMutation, type FindingExitVars } from '../core/finding.mutations';
import { type FindingTriageVerb } from '../core/finding-list';

import { GardeningFindingTriageDialogView } from './gardening-finding-triage-dialog-view';

/**
 * The findings triage dialog container — one dialog for every verb
 * {@link FindingTriageVerb} names, submitted through the `finding.mutations.ts`
 * mutation {@link verb} picks out via {@link mutationsByVerb}. A rejected
 * submission surfaces through {@link submitError} and does not close the dialog.
 */
@Component({
  selector: 'app-gardening-finding-triage-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GardeningFindingTriageDialogView],
  templateUrl: './gardening-finding-triage-dialog.html',
})
export class GardeningFindingTriageDialog {
  readonly verb = input.required<FindingTriageVerb>();
  readonly findingIds = input.required<readonly string[]>();

  readonly closed = output<void>();

  /** Emitted once the batch actually lands — distinct from {@link closed}, which
   * also fires on a plain cancel. */
  readonly succeeded = output<void>();

  private readonly resolveMutation = injectResolveFindingsMutation();
  private readonly confirmGoneMutation = injectConfirmGoneFindingsMutation();
  private readonly wontFixMutation = injectWontFixFindingsMutation();
  private readonly notAFindingMutation = injectNotAFindingFindingsMutation();
  private readonly supersedeMutation = injectSupersedeFindingsMutation();
  private readonly reopenMutation = injectReopenFindingsMutation();

  /** One entry per verb, each closing over its own injected mutation —
   * {@link submitting} reads `isPending()` off the entry {@link verb} names,
   * {@link onSubmit} calls its `mutate`. `supersededBy` is threaded through
   * uniformly; only `supersede`'s entry actually uses it. */
  private readonly mutationsByVerb: Record<
    FindingTriageVerb,
    {
      isPending(): boolean;
      mutate(
        vars: FindingExitVars,
        supersededBy: string | undefined,
        opts: { onSuccess: () => void; onError: (error: unknown) => void },
      ): void;
    }
  > = {
    resolve: {
      isPending: () => this.resolveMutation.isPending(),
      mutate: (vars, _supersededBy, opts) => this.resolveMutation.mutate(vars, opts),
    },
    'confirm-gone': {
      isPending: () => this.confirmGoneMutation.isPending(),
      mutate: (vars, _supersededBy, opts) => this.confirmGoneMutation.mutate(vars, opts),
    },
    'wont-fix': {
      isPending: () => this.wontFixMutation.isPending(),
      mutate: (vars, _supersededBy, opts) => this.wontFixMutation.mutate(vars, opts),
    },
    'not-a-finding': {
      isPending: () => this.notAFindingMutation.isPending(),
      mutate: (vars, _supersededBy, opts) => this.notAFindingMutation.mutate(vars, opts),
    },
    supersede: {
      isPending: () => this.supersedeMutation.isPending(),
      mutate: (vars, supersededBy, opts) =>
        this.supersedeMutation.mutate({ ...vars, supersededBy: supersededBy ?? '' }, opts),
    },
    reopen: {
      isPending: () => this.reopenMutation.isPending(),
      mutate: (vars, _supersededBy, opts) => this.reopenMutation.mutate(vars, opts),
    },
  };

  protected readonly submitting = computed(() => this.mutationsByVerb[this.verb()].isPending());
  protected readonly submitError = signal<string | null>(null);

  protected onSubmit(note: string, supersededBy?: string): void {
    this.submitError.set(null);
    const verb = this.verb();
    const onSuccess = () => {
      this.succeeded.emit();
      this.closed.emit();
    };
    const onError = (error: unknown) => this.submitError.set(errorMessage(error, `${verb} failed.`));
    this.mutationsByVerb[verb].mutate({ findingIds: this.findingIds(), note }, supersededBy, { onSuccess, onError });
  }
}

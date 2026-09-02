import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import {
  errorMessage,
  injectConfirmGoneFindingsMutation,
  injectNotAFindingFindingsMutation,
  injectReopenFindingsMutation,
  injectResolveFindingsMutation,
  injectSupersedeFindingsMutation,
  injectWontFixFindingsMutation,
  type FindingExitVars,
  type FindingTriageVerb,
} from 'fleet';

import { GardeningFindingTriageDialogView } from './gardening-finding-triage-dialog-view';

/**
 * The findings triage bucket's bulk-action dialog container — one dialog for
 * every verb {@link FindingTriageVerb} names, submitted
 * through whichever of the six `finding.mutations.ts` mutations {@link verb}
 * picks out (`gardening-proposal-pass-dialog.ts`'s own shape). All six are
 * injected unconditionally at field-initializer time — the same DI context every
 * one of these `inject*` calls already runs in, not a conditional hook call.
 *
 * {@link mutationsByVerb} collapses `isPending()` and the actual `.mutate(...)`
 * call for all six into one `Record<FindingTriageVerb, …>`, built once, rather
 * than two parallel six-arm `switch`es that would otherwise have to be kept in
 * sync by hand. `supersede`'s vars carry `supersededBy` where every other verb's
 * don't, so each entry's own `mutate` closes over its own mutation and folds that
 * field in only where it applies — {@link onSubmit} passes it through unconditionally,
 * `undefined` for the five verbs that ignore it.
 *
 * A rejected batch (D5) surfaces through this container's own `submitError` and
 * does **not** close the dialog — the container's caller keeps the same
 * `findingIds` selection in hand (`gardening-runs-findings-page.ts`'s own
 * `triagingBulkAction` signal is untouched by an error), so the same batch is one
 * click away from a retry.
 *
 * The host page mounts this with `@if` around the bulk action in flight
 * (`gardening-proposals-page.ts`'s own dialog-open signal shape), so a fresh
 * instance — and a fresh view, with its own fresh note field — exists for every
 * open.
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
   * also fires on a plain cancel; the host page's own selection-clear (F1) fires
   * off this, never off `closed` alone. */
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

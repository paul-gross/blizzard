import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import {
  errorMessage,
  injectConfirmGoneFindingsMutation,
  injectNotAFindingFindingsMutation,
  injectReopenFindingsMutation,
  injectResolveFindingsMutation,
  injectSupersedeFindingsMutation,
  injectWontFixFindingsMutation,
  type FindingTriageVerb,
} from 'fleet';

import { GardeningFindingTriageDialogView } from './gardening-finding-triage-dialog-view';

/**
 * The findings triage bucket's bulk-action dialog container (blizzard#401 Phase
 * 3) — one dialog for every verb {@link FindingTriageVerb} names, submitted
 * through whichever of the six `finding.mutations.ts` mutations {@link verb}
 * picks out (`gardening-proposal-pass-dialog.ts`'s own shape). All six are
 * injected unconditionally at field-initializer time — the same DI context every
 * one of these `inject*` calls already runs in, not a conditional hook call.
 *
 * {@link mutationFor} answers `isPending()` alone (every one of the six shares
 * that surface); firing the actual `.mutate(...)` is its own `switch` in
 * {@link onSubmit}, since `supersede`'s vars carry `supersededBy` where every
 * other verb's don't — a single shared call site would have to satisfy both
 * shapes at once.
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

  private readonly resolveMutation = injectResolveFindingsMutation();
  private readonly confirmGoneMutation = injectConfirmGoneFindingsMutation();
  private readonly wontFixMutation = injectWontFixFindingsMutation();
  private readonly notAFindingMutation = injectNotAFindingFindingsMutation();
  private readonly supersedeMutation = injectSupersedeFindingsMutation();
  private readonly reopenMutation = injectReopenFindingsMutation();

  protected readonly submitting = computed(() => this.mutationFor(this.verb()).isPending());
  protected readonly submitError = signal<string | null>(null);

  /** The one mutation {@link verb} names, narrowed only to the surface every one
   * of the six shares (`isPending()`) — {@link onSubmit}'s own `switch` fires the
   * actual `.mutate(...)` call, since the vars shape differs for `supersede`. */
  private mutationFor(verb: FindingTriageVerb): { isPending(): boolean } {
    switch (verb) {
      case 'resolve':
        return this.resolveMutation;
      case 'confirm-gone':
        return this.confirmGoneMutation;
      case 'wont-fix':
        return this.wontFixMutation;
      case 'not-a-finding':
        return this.notAFindingMutation;
      case 'supersede':
        return this.supersedeMutation;
      case 'reopen':
        return this.reopenMutation;
    }
  }

  protected onSubmit(note: string, supersededBy?: string): void {
    this.submitError.set(null);
    const verb = this.verb();
    const findingIds = this.findingIds();
    const onSuccess = () => this.closed.emit();
    const onError = (error: unknown) => this.submitError.set(errorMessage(error, `${verb} failed.`));
    switch (verb) {
      case 'resolve':
        this.resolveMutation.mutate({ findingIds, note }, { onSuccess, onError });
        return;
      case 'confirm-gone':
        this.confirmGoneMutation.mutate({ findingIds, note }, { onSuccess, onError });
        return;
      case 'wont-fix':
        this.wontFixMutation.mutate({ findingIds, note }, { onSuccess, onError });
        return;
      case 'not-a-finding':
        this.notAFindingMutation.mutate({ findingIds, note }, { onSuccess, onError });
        return;
      case 'supersede':
        this.supersedeMutation.mutate({ findingIds, note, supersededBy: supersededBy ?? '' }, { onSuccess, onError });
        return;
      case 'reopen':
        this.reopenMutation.mutate({ findingIds, note }, { onSuccess, onError });
        return;
    }
  }
}

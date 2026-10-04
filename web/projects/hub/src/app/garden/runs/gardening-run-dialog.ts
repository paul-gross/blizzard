import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import { asyncStateOf, errorMessage, type RoutineRunResponse, type ScopeView } from 'fleet';
import { injectHubRoutineBaselinesQuery } from './routine-baselines.query';
import { injectHubRoutineScopesQuery } from '../core/routines.query';
import { injectHubScopesQuery } from '../core/scopes.query';
import { injectRunRoutineMutation } from './routine-run.mutations';

import { GardeningRunDialogView, type RunSubmission } from './gardening-run-dialog-view';
import { liveRelatedScopes, orderScopesBySwept, sweptScopeSlugs } from './gardening-run-dialog.model';

/**
 * The gardening run dialog's container — kicks off a routine run
 * from a dialog: scope, mode, and a charge note, with the baseline read
 * (`GET /api/routines/{routine_id}/baselines`) resolving before submission rather
 * than after.
 *
 * Injects `injectHubScopesQuery` (every scope, for descriptions),
 * `injectHubRoutineScopesQuery` (the routine's own related set),
 * `injectHubRoutineBaselinesQuery`, and `injectRunRoutineMutation`; composes their data
 * into the previously-swept-first scope ordering, restricted to the routine's related, non-retired
 * scopes, and delegates every field and the submission flow to
 * {@link GardeningRunDialogView} (`bzh:frontend-container-presentational`).
 *
 * The host page mounts this with `@if` around the selected routine (the routine
 * panel's own `run` output), so a fresh instance — and a fresh view, with its own
 * fresh form signals — exists for every open; nothing here needs to reset a stale
 * field on close.
 */
@Component({
  selector: 'app-gardening-run-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GardeningRunDialogView],
  templateUrl: './gardening-run-dialog.html',
})
export class GardeningRunDialog {
  readonly routineId = input.required<string>();
  readonly routineName = input.required<string>();

  readonly closed = output<void>();

  protected readonly scopesQuery = injectHubScopesQuery();
  protected readonly routineScopesQuery = injectHubRoutineScopesQuery(() => this.routineId());
  protected readonly baselinesQuery = injectHubRoutineBaselinesQuery(() => this.routineId());
  private readonly runMutation = injectRunRoutineMutation();

  /** The routine's own related set, read off `GET /api/routines/{id}/scopes` —
   * slugs only, joined below against the all-scopes read for descriptions. */
  private readonly relatedSlugs = computed<ReadonlySet<string>>(
    () => new Set(this.routineScopesQuery.data() ?? []),
  );

  /** The routine's related, non-retired scopes — a retired scope stays related
   * but is offered to no run. */
  private readonly liveScopes = computed<readonly ScopeView[]>(() =>
    liveRelatedScopes(this.scopesQuery.data() ?? [], this.relatedSlugs()),
  );

  /** The scope slugs this routine has swept. */
  protected readonly sweptSlugs = computed<ReadonlySet<string>>(() => sweptScopeSlugs(this.baselinesQuery.data() ?? []));

  /** Previously-swept scopes first, in newest-swept-first order; every other
   * live, related scope after, in the order `GET /api/scopes` served them. */
  protected readonly orderedScopes = computed<readonly ScopeView[]>(() =>
    orderScopesBySwept(this.liveScopes(), this.baselinesQuery.data() ?? [], this.sweptSlugs()),
  );

  /** `isEmpty` reads the related set's own literal count — never hardcoded. */
  protected readonly state = computed(() =>
    asyncStateOf([this.scopesQuery, this.routineScopesQuery, this.baselinesQuery], this.liveScopes().length === 0),
  );

  protected readonly submitting = computed(() => this.runMutation.isPending());

  protected readonly submitError = signal<string | null>(null);

  protected readonly confirmedRun = signal<RoutineRunResponse | null>(null);

  protected onSubmit(submission: RunSubmission): void {
    this.submitError.set(null);
    this.runMutation.mutate(
      { routineId: this.routineId(), scopeSlug: submission.selection, mode: submission.mode, note: submission.note },
      {
        onSuccess: (data) => this.confirmedRun.set(data),
        onError: (error) => this.submitError.set(errorMessage(error, 'Run failed.')),
      },
    );
  }
}

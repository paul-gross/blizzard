import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import {
  asyncStateOf,
  errorMessage,
  injectCreateScopeMutation,
  injectHubRoutineBaselinesQuery,
  injectHubScopesQuery,
  injectRunRoutineMutation,
  type RoutineRunResponse,
  type ScopeView,
} from 'fleet';

import { GardeningRunDialogView, type RunSubmission } from './gardening-run-dialog-view';

/**
 * The gardening run dialog's container (blizzard#392 D6) — kicks off a routine run
 * from a dialog: scope, mode, and a charge note, with the baseline read
 * (`GET /api/routines/{routine_id}/baselines`, D5) resolving before submission rather
 * than after.
 *
 * Injects `injectHubScopesQuery`, `injectHubRoutineBaselinesQuery`, and the two
 * mutations (`injectCreateScopeMutation`, `injectRunRoutineMutation`); composes their
 * data into the scope ordering D5 names and delegates every field and the submission
 * flow to {@link GardeningRunDialogView} (`bzh:frontend-container-presentational`).
 *
 * The host page mounts this with `@if` around the selected routine (Phase 5's
 * trigger), so a fresh instance — and a fresh view, with its own fresh form signals —
 * exists for every open; nothing here needs to reset a stale field on close.
 */
@Component({
  selector: 'app-gardening-run-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GardeningRunDialogView],
  templateUrl: './gardening-run-dialog.html',
})
export class GardeningRunDialog {
  readonly open = input.required<boolean>();
  readonly routineId = input.required<string>();
  readonly routineName = input.required<string>();

  readonly closed = output<void>();

  protected readonly scopesQuery = injectHubScopesQuery();
  protected readonly baselinesQuery = injectHubRoutineBaselinesQuery(() => this.routineId());
  private readonly createScopeMutation = injectCreateScopeMutation();
  private readonly runMutation = injectRunRoutineMutation();

  /** Every non-retired scope (D6's own reading of "the scope picker lists every
   * non-retired scope") — a scope this chunk's issue owns no verb to un-retire
   * inline, so a retired one is simply never offered. */
  private readonly liveScopes = computed<readonly ScopeView[]>(() => (this.scopesQuery.data() ?? []).filter((s) => !s.retired));

  /** The scope slugs this routine has swept, D5's own read. */
  protected readonly sweptSlugs = computed<ReadonlySet<string>>(
    () => new Set((this.baselinesQuery.data() ?? []).map((b) => b.scope_slug)),
  );

  /** Previously-swept scopes first, in D5's own newest-swept-first order; every other
   * live scope after, in the order `GET /api/scopes` served them (D5's own ordering
   * criterion). */
  protected readonly orderedScopes = computed<readonly ScopeView[]>(() => {
    const swept = this.sweptSlugs();
    const bySlug = new Map(this.liveScopes().map((s) => [s.slug, s]));
    const sweptOrdered = (this.baselinesQuery.data() ?? [])
      .map((b) => bySlug.get(b.scope_slug))
      .filter((s): s is ScopeView => s !== undefined);
    const rest = this.liveScopes().filter((s) => !swept.has(s.slug));
    return [...sweptOrdered, ...rest];
  });

  protected readonly state = computed(() => asyncStateOf([this.scopesQuery, this.baselinesQuery], false));

  protected readonly submitting = computed(() => this.createScopeMutation.isPending() || this.runMutation.isPending());

  protected readonly submitError = signal<string | null>(null);

  protected readonly confirmedRun = signal<RoutineRunResponse | null>(null);

  /** D3's create-then-run ordering: a new slug is minted through `POST /api/scopes`,
   * with its description, before the run — never left to the run route's own
   * empty-description mint. A create that fails surfaces its own refusal and never
   * reaches the run at all. */
  protected onSubmit(submission: RunSubmission): void {
    this.submitError.set(null);
    const { selection, mode, note } = submission;
    if (selection.isNew) {
      this.createScopeMutation.mutate(
        { slug: selection.slug, description: selection.newDescription },
        {
          onSuccess: () => this.runNow(selection.slug, mode, note),
          onError: (error) => this.submitError.set(errorMessage(error, 'Could not create the scope.')),
        },
      );
      return;
    }
    this.runNow(selection.slug, mode, note);
  }

  private runNow(scopeSlug: string, mode: 'full' | 'delta', note: string | null): void {
    this.runMutation.mutate(
      { routineId: this.routineId(), scopeSlug, mode, note },
      {
        onSuccess: (data) => this.confirmedRun.set(data),
        onError: (error) => this.submitError.set(errorMessage(error, 'Run failed.')),
      },
    );
  }
}

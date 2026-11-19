import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal } from '@angular/core';
import { apply, form } from '@angular/forms/signals';

import { asyncStateOf, createRefusalState, type hubApi, type RoutineBaselineView, type RoutineRunResponse, type ScopeView, submitForm } from 'fleet';
import { injectHubRoutineBaselinesQuery } from './routine-baselines.query';
import { injectHubRoutineScopesQuery } from '../core/routines.query';
import { injectUrgenciesQuery } from '../../core/urgencies.query';
import { injectHubScopesQuery } from '../core/scopes.query';
import { EMPTY_RUN_DRAFT, type RunDraft } from '../core/run.domain';
import { runRules } from './run.form';
import { injectRunService } from './run.service';

import { GardeningRunDialogView } from './gardening-run-dialog-view';
import { baselineForScope, liveRelatedScopes, orderScopesBySwept, sweptScopeSlugs } from './gardening-run-dialog.model';

/**
 * The gardening run dialog's container — kicks off a routine run
 * from a dialog: scope, mode, and a charge note, with the baseline read
 * (`GET /api/routines/{routine_id}/baselines`) resolving before submission rather
 * than after.
 *
 * Injects `injectHubScopesQuery` (every scope, for descriptions),
 * `injectHubRoutineScopesQuery` (the routine's own related set),
 * `injectHubRoutineBaselinesQuery`, and the run service; composes their data
 * into the previously-swept-first scope ordering, restricted to the routine's related, non-retired
 * scopes, owns the run draft, its rules, and the save, and renders through
 * {@link GardeningRunDialogView} (`bzh:frontend-container-presentational`).
 *
 * The host page mounts this with `@if` around the selected routine (the routine
 * panel's own `run` output), so a fresh instance — with a fresh draft — exists for
 * every open; nothing here needs to reset a stale field on close.
 */
@Component({
  selector: 'app-gardening-run-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GardeningRunDialogView],
  templateUrl: './gardening-run-dialog.html',
})
export class GardeningRunDialog {
  protected readonly urgenciesQuery = injectUrgenciesQuery();
  readonly effectiveUrgency = input<hubApi.UrgencyRef | null>(null);
  readonly routineId = input.required<string>();
  readonly routineName = input.required<string>();

  readonly closed = output<void>();

  protected readonly scopesQuery = injectHubScopesQuery();
  protected readonly routineScopesQuery = injectHubRoutineScopesQuery(() => this.routineId());
  protected readonly baselinesQuery = injectHubRoutineBaselinesQuery(() => this.routineId());

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

  private readonly runService = injectRunService();
  protected readonly submitting = this.runService.pending;

  protected readonly confirmedRun = signal<RoutineRunResponse | null>(null);

  private readonly draft = signal<RunDraft>(EMPTY_RUN_DRAFT);
  private readonly refusal = createRefusalState<RunDraft>();
  private readonly rules = runRules(
    () => new Set(this.orderedScopes().map((scope) => scope.slug)),
    () => this.sweptSlugs(),
  );
  protected readonly runForm = form(this.draft, (p) => {
    apply(p, this.rules);
    apply(p, this.refusal.schema);
  });

  /** The delta baseline for the chosen scope, or `undefined` for a never-swept pair or no choice yet. */
  protected readonly baseline = computed<RoutineBaselineView | undefined>(() =>
    baselineForScope(this.baselinesQuery.data() ?? [], this.draft().scope),
  );

  constructor() {
    // Defaults the choice to the first (previously-swept-first-ordered) scope the instant the read resolves — a bare
    // empty choice otherwise leaves nothing checked until the operator acts.
    effect(() => {
      if (this.state() !== 'ready') return;
      const scopes = this.orderedScopes();
      if (scopes.length > 0 && !scopes.some((scope) => scope.slug === this.draft().scope)) {
        this.draft.update((d) => ({ ...d, scope: scopes[0].slug }));
      }
    });
    // The delta-steering rule: a scope that carries no baseline — the operator switched to a never-swept one — steers
    // back to full rather than leaving delta chosen with nothing to run it against.
    effect(() => {
      if (this.baseline() === undefined && this.draft().mode === 'delta') this.draft.update((d) => ({ ...d, mode: 'full' }));
    });
  }

  protected async onSave(): Promise<void> {
    if (this.submitting() || this.state() !== 'ready') return;
    await submitForm(this.runForm, async (draft) => {
      const outcome = await this.runService.run(this.routineId(), draft);
      if (outcome.ok) this.confirmedRun.set(outcome.run);
      return outcome;
    }, { refusalState: this.refusal });
  }
}

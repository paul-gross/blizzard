import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import {
  asyncState,
  errorMessage,
  FleetScopePanel,
  hasPermission,
  injectEditScopeMutation,
  injectHubRoutinesQuery,
  injectHubScopeRoutinesQuery,
  injectHubScopesQuery,
  injectMeQuery,
  injectPendingMutationVariables,
  injectScopeLifecycleMutation,
  scopeLifecycleMutationKey,
  type KitAsyncStateValue,
  type RelatedRoutineVm,
  type RoutineView,
  type ScopeDescriptionEditEvent,
  type ScopeLifecycleVars,
  type ScopePanelVm,
  type ScopeView,
} from 'fleet';
import { map } from 'rxjs';

/**
 * The selected scope's own detail — the right-hand child of `/gardening/scopes`
 * (`gardening-scopes-page.ts` owns the list beside it). Mounted by both of that
 * route's children: the bare one, where it renders its own "nothing selected"
 * empty state, and `:scopeSlug`.
 *
 * A container: it injects the reads and the two write mutations, and forwards a
 * plain view model to the presentational {@link FleetScopePanel}. It shares no
 * state with the list beside it — every read here is the same cache-keyed query
 * that list already holds, so resolving the routed scope independently costs no
 * second fetch and keeps the two halves free of a seam between them.
 *
 * The routines read is not dead weight even though this pane shows no routine of
 * its own: `FleetScopePanel` renders the routines related to the selected scope
 * (`scopePanelVm`'s own `relatedRoutines`), resolving each id the dedicated
 * scope-routines read returns into a name and its default-ness off this already-held
 * list — do not "clean up" what looks like an unused query.
 *
 * Scopes are editable in place and retire/enable-able, gated on `graph:edit` (the
 * same permission `src/blizzard/hub/api/scopes.py` requires) — `graph-detail.ts`'s
 * own `canEdit`/`actionError` shape, transliterated to scopes.
 */
@Component({
  selector: 'app-gardening-scope-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetScopePanel],
  templateUrl: './gardening-scope-detail.html',
  styleUrl: './gardening-detail-host.css',
})
export class GardeningScopeDetail {
  private readonly route = inject(ActivatedRoute);

  private readonly routinesQuery = injectHubRoutinesQuery();
  private readonly scopesQuery = injectHubScopesQuery();
  private readonly meQuery = injectMeQuery();
  private readonly editScopeMutation = injectEditScopeMutation();
  private readonly scopeLifecycleMutation = injectScopeLifecycleMutation();

  /** Every scope slug a Retire/Enable mutation is currently pending for, and its own
   * variables (`bzh:frontend-pending-override`) — read through the shared helper
   * rather than `scopeLifecycleMutation.isPending()` alone, since {@link
   * overrideRetired} below needs the fired *direction* (`retired: true` vs.
   * `false`), not just pending-ness (`chunk-detail.ts`'s own `pendingChunkPauses`
   * shape). This pane shows exactly one scope at a time, so there is no sibling row
   * to distinguish pending mutations by variables the way a list surface would —
   * {@link lifecyclePending} below still reads the mutation's bare `isPending()` for
   * that reason, `chunk-detail.ts`'s own `pausePending` shape. */
  private readonly pendingScopeLifecycle = injectPendingMutationVariables<ScopeLifecycleVars>(scopeLifecycleMutationKey);

  private readonly routines = computed<readonly RoutineView[]>(() => this.routinesQuery.data() ?? []);
  private readonly scopes = computed<readonly ScopeView[]>(() => this.scopesQuery.data() ?? []);

  /** The `scopeSlug` route param, or `null` on the bare child route. */
  private readonly scopeSlug = toSignal(this.route.paramMap.pipe(map((params) => params.get('scopeSlug'))), {
    initialValue: null,
  });

  private readonly selectedScope = computed<ScopeView | null>(() => {
    const slug = this.scopeSlug();
    return slug === null ? null : (this.scopes().find((s) => s.slug === slug) ?? null);
  });

  private readonly scopeRoutinesQuery = injectHubScopeRoutinesQuery(() => this.selectedScope()?.slug ?? null);

  /** Whether the current identity may author scopes (`graph:edit`, admin-tier — the
   * same permission the scope write routes require server-side); `null`/pending
   * resolves to `false`, `graph-detail.ts`'s own `canEdit`. */
  protected readonly canEditScopes = computed(() => hasPermission(this.meQuery.data(), 'graph:edit'));

  /** The selected scope's related routines, each resolved to a name and its
   * default-ness off the already-held routines list (D2) — `null` until both that
   * list and the scope-routines read resolve (D5): gating on the id read alone would
   * let it settle first and render every entry as its bare id with no name and no
   * default marked, which self-corrects once `routinesQuery` catches up but reads as
   * a wrong, settled answer in between. */
  private readonly relatedRoutines = computed<readonly RelatedRoutineVm[] | null>(() => {
    const ids = this.scopeRoutinesQuery.data();
    const scope = this.selectedScope();
    if (ids === undefined || scope === null || this.routinesQuery.data() === undefined) return null;
    const routinesById = new Map(this.routines().map((r) => [r.routine_id, r]));
    return ids.map((id) => {
      const routine = routinesById.get(id);
      return { name: routine?.name ?? id, isDefault: routine?.default_scope_slug === scope.slug };
    });
  });

  /**
   * The selected scope's `retired` flag as it will read once a currently pending
   * Retire/Enable settles (`bzh:frontend-pending-override`) — `null` while nothing
   * overrides `scope.retired`, computed purely off {@link pendingScopeLifecycle}'s
   * own variables, never a cache read or write.
   *
   * **Total in both directions.** A scope's lifecycle carries exactly the two states
   * `domain/routines-and-scopes.md`'s "The retired brake" section names — `retired`
   * and enabled — with no third state or precedence rule complicating either
   * transition (unlike `chunk-detail.ts`'s Pause, which a human-gated status can
   * outrank): `retire` and `enable` are each reachable from the other unconditionally,
   * so the fired direction is always the resulting one, the same strong case
   * `graph-lifecycle.mutations.ts`'s own `retired` boolean rides.
   */
  protected readonly overrideRetired = computed<boolean | null>(() => {
    const scope = this.selectedScope();
    if (scope === null) return null;
    const pending = this.pendingScopeLifecycle().find((vars) => vars.slug === scope.slug);
    return pending ? pending.retired : null;
  });

  /** The selected scope's panel view model. */
  protected readonly scopePanelVm = computed<ScopePanelVm | null>(() => {
    const scope = this.selectedScope();
    if (scope === null) return null;
    return {
      slug: scope.slug,
      description: scope.description,
      retired: this.overrideRetired() ?? (scope.retired ?? false),
      relatedRoutines: this.relatedRoutines(),
    };
  });

  /** "Nothing selected" is its own rest state, branched before the read's own
   * pending/error/empty triad (`bzh:frontend-empty-state-gated`). */
  protected readonly scopePanelState = computed<KitAsyncStateValue>(() =>
    this.scopeSlug() === null ? 'empty' : asyncState(this.scopesQuery, this.selectedScope() === null),
  );

  /** Set on a failed edit/retire/enable; cleared at the start of the next attempt. */
  protected readonly scopeActionError = signal<string | null>(null);

  /** Whether the edit-description mutation is in flight for this scope, threaded to
   * {@link FleetScopePanel}'s Set button (`graph-detail.ts`'s own `.isPending()`
   * shape, transliterated to scopes). */
  protected readonly editPending = computed(() => this.editScopeMutation.isPending());

  /** Whether the retire/enable mutation is in flight for this scope, threaded to
   * {@link FleetScopePanel}'s Re-enable/Retire buttons — only one of the two is ever
   * shown for the scope's current lifecycle state, so disabling both while either is
   * in flight is correct. */
  protected readonly lifecyclePending = computed(() => this.scopeLifecycleMutation.isPending());

  protected onEditScopeDescription(event: ScopeDescriptionEditEvent): void {
    this.scopeActionError.set(null);
    this.editScopeMutation.mutate(event, {
      onError: (error: unknown) => this.scopeActionError.set(errorMessage(error, 'Set description failed.')),
    });
  }

  protected onRetireScope(slug: string): void {
    this.scopeActionError.set(null);
    this.scopeLifecycleMutation.mutate(
      { slug, retired: true },
      { onError: (error: unknown) => this.scopeActionError.set(errorMessage(error, 'Retire failed.')) },
    );
  }

  protected onEnableScope(slug: string): void {
    this.scopeActionError.set(null);
    this.scopeLifecycleMutation.mutate(
      { slug, retired: false },
      { onError: (error: unknown) => this.scopeActionError.set(errorMessage(error, 'Enable failed.')) },
    );
  }
}

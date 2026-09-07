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
  injectScopeLifecycleMutation,
  type KitAsyncStateValue,
  type RelatedRoutineVm,
  type RoutineView,
  type ScopeDescriptionEditEvent,
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

  /** The selected scope's panel view model. */
  protected readonly scopePanelVm = computed<ScopePanelVm | null>(() => {
    const scope = this.selectedScope();
    if (scope === null) return null;
    return {
      slug: scope.slug,
      description: scope.description,
      retired: scope.retired ?? false,
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

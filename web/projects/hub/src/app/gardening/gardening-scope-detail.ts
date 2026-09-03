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
  injectHubScopesQuery,
  injectMeQuery,
  injectScopeLifecycleMutation,
  type KitAsyncStateValue,
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
 * its own: `FleetScopePanel` shows which routines default to the selected scope
 * (`scopePanelVm`'s own `defaultingRoutineNames`) — do not "clean up" what looks
 * like an unused query.
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

  /** Whether the current identity may author scopes (`graph:edit`, admin-tier — the
   * same permission the scope write routes require server-side); `null`/pending
   * resolves to `false`, `graph-detail.ts`'s own `canEdit`. */
  protected readonly canEditScopes = computed(() => hasPermission(this.meQuery.data(), 'graph:edit'));

  /** The selected scope's panel view model — `defaultingRoutineNames` is free: the
   * routines query is fetched purely to serve it. */
  protected readonly scopePanelVm = computed<ScopePanelVm | null>(() => {
    const scope = this.selectedScope();
    if (scope === null) return null;
    return {
      slug: scope.slug,
      description: scope.description,
      retired: scope.retired ?? false,
      defaultingRoutineNames: this.routines()
        .filter((r) => r.default_scope_slug === scope.slug)
        .map((r) => r.name),
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

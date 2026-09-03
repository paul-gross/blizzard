import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import {
  asyncState,
  FleetScopeList,
  injectHubScopesQuery,
  KitPanel,
  type KitAsyncStateValue,
  type ScopeRowVm,
  type ScopeView,
} from 'fleet';

import { injectChildRouteParam } from '../route-state';

/**
 * The `/gardening/scopes` sub-tab (`plans/garden/user-interface.md` §Declaring and
 * running a routine) — the scope list, beside a `<router-outlet>` holding whichever
 * scope the URL names (`gardening-scope-detail.ts`). Split off
 * `gardening-routines-page.ts`'s combined routines-and-scopes surface
 * (blizzard#399/#397); routines and scopes are unrelated concepts that only used to
 * share a tab.
 *
 * The list is the parent route and the detail its child, so picking a row swaps only
 * the pane beside the list: this component, its scroll position, and any filter state
 * a sibling tab holds all survive a selection where a flat pair of routes onto one
 * component would have rebuilt them (`route-state.ts`). It owns the right column's
 * panel chrome too, so the frame around the detail never blinks on a pick.
 *
 * A container: it injects the scope query and forwards plain view models to the
 * presentational {@link FleetScopeList}, which injects no query of its own.
 */
@Component({
  selector: 'app-gardening-scopes-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetScopeList, KitPanel, RouterOutlet],
  templateUrl: './gardening-scopes-page.html',
  styleUrl: './gardening-scopes-page.css',
})
export class GardeningScopesPage {
  private readonly router = inject(Router);

  private readonly scopesQuery = injectHubScopesQuery();
  private readonly scopes = computed<readonly ScopeView[]>(() => this.scopesQuery.data() ?? []);

  /** The `scopeSlug` the active detail child names — the selection lives on that
   * child's route, not this one's (`route-state.ts`). A scope has no id of its own;
   * its slug *is* the id (`foundation/ids.py`). */
  private readonly scopeSlugParam = injectChildRouteParam('scopeSlug');

  /** The effective selection: the route param if it still names a scope the loaded
   * data actually has, else `null` — never a stale highlight left over from a scope
   * that no longer exists. */
  protected readonly selectedScopeSlug = computed<string | null>(() => {
    const scopeSlug = this.scopeSlugParam();
    if (scopeSlug === null) return null;
    return this.scopes().some((s) => s.slug === scopeSlug) ? scopeSlug : null;
  });

  protected selectScope(slug: string): void {
    void this.router.navigate(['/gardening', 'scopes', slug], { queryParamsHandling: 'preserve' });
  }

  protected readonly scopeRows = computed<readonly ScopeRowVm[]>(() =>
    this.scopes().map((s) => ({ slug: s.slug, description: s.description, retired: s.retired ?? false })),
  );

  protected readonly scopesState = computed<KitAsyncStateValue>(() =>
    asyncState(this.scopesQuery, this.scopeRows().length === 0),
  );
}

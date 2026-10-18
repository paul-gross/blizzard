import { ChangeDetectionStrategy, Component, computed, effect, inject } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { FLEET_CLOCK, asyncState, KitMasterDetail, KitPanel, type KitAsyncStateValue, type ScopeView, ViewportService } from 'fleet';
import { FleetScopeList, type ScopeRowVm } from './scope-list';
import { injectHubScopesQuery } from '../core/scopes.query';

import { injectChildRouteParam } from '../../core/route-state';
import { presentScopeSlug, scopeRows } from './gardening-scopes-page.model';
import { ProjectLens } from '../../core/project-lens';
import { injectLensedProject } from '../../core/lensed-project';
import { firstRowToOpen } from '../../core/first-row';
import { defaultRoutineWindow } from '../core/routine-window';
import { injectHubRunsQuery } from '../runs/garden-runs.query';

/**
 * The `/gardening/scopes` sub-tab (`blizzard-product:/delivered/garden/user-interface.md` §Declaring
 * and running a routine) — the scope list, beside a `<router-outlet>` holding whichever
 * scope the URL names (`gardening-scope-detail.ts`).
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
  imports: [FleetScopeList, KitMasterDetail, KitPanel, RouterOutlet],
  templateUrl: './gardening-scopes-page.html',
  styleUrl: './gardening-scopes-page.css',
})
export class GardeningScopesPage {
  private readonly lens = inject(ProjectLens);

  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');

  private readonly scopesQuery = injectHubScopesQuery();
  private readonly window = defaultRoutineWindow(inject(FLEET_CLOCK)());
  private readonly runsQuery = injectHubRunsQuery(() => this.window.since);
  private readonly project = injectLensedProject();

  /** The project every scope listed here belongs to: its name once read, its slug until then. */
  protected readonly projectName = computed(() => this.project()?.name ?? this.lens.slug() ?? '');
  private readonly scopes = computed<readonly ScopeView[]>(() => this.scopesQuery.data() ?? []);

  /** The `scopeSlug` the active detail child names — the selection lives on that
   * child's route, not this one's (`route-state.ts`). A scope has no id of its own;
   * its slug *is* the id. */
  protected readonly scopeSlugParam = injectChildRouteParam('scopeSlug');

  /** The effective selection: the route param if it still names a scope the loaded
   * data actually has, else `null` — never a stale highlight left over from a scope
   * that no longer exists. */
  protected readonly selectedScopeSlug = computed<string | null>(() =>
    presentScopeSlug(this.scopeSlugParam(), this.scopes()),
  );

  protected selectScope(slug: string): void {
    void this.router.navigate(this.lens.link(['/gardening', 'scopes', slug]), { queryParamsHandling: 'preserve' });
  }

  protected onBack(): void {
    void this.router.navigate(this.lens.link(['/gardening', 'scopes']), { queryParamsHandling: 'preserve' });
  }

  protected readonly scopeRows = computed<readonly ScopeRowVm[]>(() => scopeRows(this.scopes(), this.runsQuery.data(), this.window));

  protected readonly scopesState = computed<KitAsyncStateValue>(() =>
    asyncState(this.scopesQuery, this.scopeRows().length === 0),
  );

  constructor() {
    effect(() => {
      const settled = !this.scopesQuery.isPending() && !this.scopesQuery.isError();
      const slugs = this.scopeRows().map((row) => row.slug);
      const first = firstRowToOpen(this.scopeSlugParam(), slugs, settled, this.mobile());
      if (first === null) return;
      void this.router.navigate(this.lens.link(['/gardening', 'scopes', first]), {
        replaceUrl: true,
        queryParamsHandling: 'preserve',
      });
    });
  }
}

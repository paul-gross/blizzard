import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import { FLEET_CLOCK, KitMenu, KitMenuItemRadio, KitMenuPanel, KitTab, KitTabStrip } from 'fleet';
import { LensLink } from 'fleet/shell';

import { injectLensedProject } from '../core/lensed-project';
import { ProjectLens } from '../core/project-lens';
import { injectProjectsQuery } from '../core/projects.query';
import { injectHubRunnersQuery } from '../core/runners.query';
import { gardenPathOf } from './garden-lens.guard';
import { GARDENING_DOORWAY_URL } from './core/garden-project';
import { injectHubRoutinesQuery } from './core/routines.query';
import { defaultRoutineWindow } from './core/routine-window';
import { injectHubScopesQuery } from './core/scopes.query';
import {
  gardenProjectChoices,
  gardenSurfaceCounts,
  gardenSurfaceTabs,
  gardenUnserved,
  surfaceLabel,
  surfaceOf,
  waitingLabel,
} from './gardening-page.model';
import { injectHubGardenProposalsQuery } from './proposals/garden-proposals.query';
import { injectHubRunsQuery } from './runs/garden-runs.query';

/**
 * One project's garden — the `/p/{project}/gardening` route, a top-level tab beside the board, not a
 * panel inside it (`blizzard-product:/delivered/garden/user-interface.md` §Where it
 * lives). The app shell above this page is untouched; this page owns only its own
 * second strip, divided along the garden's surfaces in this fixed order — scopes, routines, runs and findings,
 * proposals — each a real child route (`gardening.routes.ts`) so a tab is a deep link, not this component's own
 * selection state. Runs and findings share a surface: a run is a sweep and its findings are what it found, so the
 * surface switches between the run list and the findings list, each its own route.
 *
 * Above the strip, the breadcrumb `Gardening › {project} › {surface}` is the lens shown in place: `Gardening` opens the
 * doorway, and the project crumb switches the lens and keeps the surface, each choice showing that project's proposals
 * waiting. A project no live runner serves says so here, since nothing its garden raises is worked until one does.
 *
 * Each tab counts its own list, from the read the list itself makes, so the two never disagree. Proposals counts the
 * ones waiting on a person and is the one count drawn in amber: scopes, routines and runs accumulate, they do not wait.
 * The strip's right-hand line says whose records these are.
 */
@Component({
  selector: 'app-gardening-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    KitMenu,
    KitMenuItemRadio,
    KitMenuPanel,
    KitTab,
    KitTabStrip,
    LensLink,
    RouterLink,
    RouterLinkActive,
    RouterOutlet,
  ],
  templateUrl: './gardening-page.html',
  styleUrl: './gardening-page.css',
})
export class GardeningPage {
  private readonly lens = inject(ProjectLens);
  private readonly router = inject(Router);
  private readonly window = defaultRoutineWindow(inject(FLEET_CLOCK)());
  private readonly scopesQuery = injectHubScopesQuery();
  private readonly routinesQuery = injectHubRoutinesQuery();
  private readonly runsQuery = injectHubRunsQuery(() => this.window.since);
  private readonly proposalsQuery = injectHubGardenProposalsQuery();
  private readonly tenantProposalsQuery = injectHubGardenProposalsQuery({ tenantWide: true });
  private readonly projectsQuery = injectProjectsQuery();
  private readonly runnersQuery = injectHubRunnersQuery();
  private readonly project = injectLensedProject();
  private readonly url = toSignal(
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      map((event) => event.urlAfterRedirects),
    ),
    { initialValue: this.router.url },
  );

  /** The doorway's address, for the `Gardening` crumb's link target. */
  protected readonly doorwayUrl = GARDENING_DOORWAY_URL;

  /** The strip's four tabs, each with its list's count. */
  protected readonly tabs = computed(() =>
    gardenSurfaceTabs(
      gardenSurfaceCounts({
        scopes: this.scopesQuery.data(),
        routines: this.routinesQuery.data(),
        runs: this.runsQuery.data(),
        proposals: this.proposalsQuery.data(),
      }),
    ),
  );

  /** The slug the lens is on — the project crumb's checked choice. */
  protected readonly lensSlug = this.lens.slug;

  /** The project crumb's text: the project's name once read, its slug until then. */
  protected readonly projectName = computed(() => this.project()?.name ?? this.lens.slug() ?? '');

  protected readonly projectChoices = computed(() =>
    gardenProjectChoices(this.projectsQuery.data() ?? [], this.tenantProposalsQuery.data()),
  );

  private readonly tab = computed(() => gardenPathOf(this.url()).tab);

  /** The surface the URL is on — the active tab. */
  protected readonly surface = computed(() => surfaceOf(this.tab()));

  /** The surface crumb: the surface's label. */
  protected readonly surfaceLabel = computed(() => surfaceLabel(this.tab()));

  protected readonly unserved = computed(() => gardenUnserved(this.project(), this.runnersQuery.data()));

  protected readonly waitingLabel = waitingLabel;

  /** Switches the lens to `slug`, keeping the surface — or opens the doorway for All. */
  protected chooseProject(slug: string | null): void {
    if (slug === this.lens.slug()) return;
    void this.lens.switchTo(slug);
  }

  /** The `Gardening` crumb: back to the doorway, on All. */
  protected openDoorway(event: Event): void {
    event.preventDefault();
    void this.router.navigateByUrl(this.doorwayUrl);
  }
}

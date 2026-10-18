import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import { FLEET_CLOCK, KitMenu, KitMenuItemRadio, KitMenuPanel, KitTab, KitTabStrip } from 'fleet';
import { LensLink } from 'fleet/shell';

import { injectLensedProject } from '../core/lensed-project';
import { ProjectLens } from '../core/project-lens';
import { injectProjectsQuery } from '../core/projects.query';
import { gardenPathOf } from './garden-lens.guard';
import { GARDENING_DOORWAY_URL } from './core/garden-project';
import { injectHubRoutinesQuery } from './core/routines.query';
import { defaultRoutineWindow } from './core/routine-window';
import { injectHubScopesQuery } from './core/scopes.query';
import {
  gardenProjectChoices,
  gardenSurfaceCounts,
  gardenSurfaceTabs,
  surfaceLabel,
  surfaceOf,
  waitingLabel,
} from './gardening-page.model';
import { injectHubGardenProposalsQuery } from './proposals/garden-proposals.query';
import { injectHubRunsQuery } from './runs/garden-runs.query';

/**
 * One project's garden — the `/p/{project}/gardening` route, a top-level tab beside the board, not a
 * panel inside it (`blizzard-product:/delivered/garden/user-interface.md` §Where it
 * lives). This page owns its own
 * second strip, divided along the garden's surfaces in this fixed order — scopes, routines, runs, findings,
 * proposals — each a real child route (`gardening.routes.ts`) so a tab is a deep link, not this component's own
 * selection state.
 *
 * Above the strip, the breadcrumb `Gardening › {project} › {surface}` is the lens shown in place: `Gardening` opens the
 * doorway, and the project crumb switches the lens and keeps the surface, each choice showing that project's proposals
 * waiting.
 *
 * Scopes, routines and runs count their lists; Proposals counts the ones waiting on a person and draws that count in
 * amber. Findings has no total badge: its list reads only the requested pages, so a loaded-row count is not a total.
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

  /** The strip's five tabs, with counts for the surfaces whose reads supply a total. */
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

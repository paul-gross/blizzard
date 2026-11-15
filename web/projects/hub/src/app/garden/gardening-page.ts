import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import { FLEET_CLOCK, KitTab, KitTabStrip } from 'fleet';
import { LensLink } from 'fleet/shell';

import { gardenPathOf } from './garden-lens.guard';
import { injectHubCadencesQuery } from './core/cadences.query';
import { injectHubRoutinesQuery } from './core/routines.query';
import { defaultRoutineWindow } from './core/routine-window';
import { injectHubScopesQuery } from './core/scopes.query';
import {
  gardenSurfaceCounts,
  gardenSurfaceTabs,
  surfaceOf,
} from './gardening-page.model';
import { injectHubGardenProposalsQuery } from './proposals/garden-proposals.query';
import { injectHubRunsQuery } from './runs/garden-runs.query';

/**
 * One project's garden — the `/p/{project}/gardening` route, a top-level tab beside the board, not a
 * panel inside it (`blizzard-product:/delivered/garden/user-interface.md` §Where it lives). This page owns its
 * own second strip, divided along the garden's surfaces in this fixed order — scopes, routines, cadences, runs,
 * findings, proposals — each a real child route (`gardening.routes.ts`) so a tab is a deep link, not this component's own
 * selection state.
 *
 * Scopes, routines, cadences and runs count their lists; Proposals counts the ones waiting on a person and draws that count in
 * amber. Findings has no total badge: its list reads only the requested pages, so a loaded-row count is not a total.
 */
@Component({
  selector: 'app-gardening-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
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
  private readonly router = inject(Router);
  private readonly window = defaultRoutineWindow(inject(FLEET_CLOCK)());
  private readonly scopesQuery = injectHubScopesQuery();
  private readonly routinesQuery = injectHubRoutinesQuery();
  private readonly cadencesQuery = injectHubCadencesQuery();
  private readonly runsQuery = injectHubRunsQuery(() => this.window.since);
  private readonly proposalsQuery = injectHubGardenProposalsQuery();
  private readonly url = toSignal(
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      map((event) => event.urlAfterRedirects),
    ),
    { initialValue: this.router.url },
  );

  /** The strip's six tabs, with counts for the surfaces whose reads supply a total. */
  protected readonly tabs = computed(() =>
    gardenSurfaceTabs(
      gardenSurfaceCounts({
        scopes: this.scopesQuery.data(),
        routines: this.routinesQuery.data(),
        cadences: this.cadencesQuery.data(),
        runs: this.runsQuery.data(),
        proposals: this.proposalsQuery.data(),
      }),
    ),
  );

  private readonly tab = computed(() => gardenPathOf(this.url()).tab);

  /** The surface the URL is on — the active tab. */
  protected readonly surface = computed(() => surfaceOf(this.tab()));

}

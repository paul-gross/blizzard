import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import { KitCountBadge, KitMenu, KitMenuItemRadio, KitMenuPanel, KitTab, KitTabStrip } from 'fleet';

import { injectLensedProject } from '../core/lensed-project';
import { ProjectLens } from '../core/project-lens';
import { injectProjectsQuery } from '../core/projects.query';
import { injectHubRunnersQuery } from '../core/runners.query';
import { gardenPathOf } from './garden-lens.guard';
import { gardenProjectChoices, gardenUnserved, surfaceLabel, waitingProposalCount } from './gardening-page.model';
import { injectHubGardenProposalsQuery } from './proposals/garden-proposals.query';

/**
 * One project's garden — the `/p/{project}/gardening` route, a top-level tab beside the board, not a
 * panel inside it (`blizzard-product:/delivered/garden/user-interface.md` §Where it
 * lives). The app shell above this page is untouched; this page owns only its own
 * second strip, divided along the garden machinery's own nouns, in this fixed order
 * — runs, findings, proposals, routines, scopes — each a real child route
 * (`gardening.routes.ts`) so a sub-tab is a deep link, not this component's own selection
 * state. All five are unrelated concepts (a scope is where a routine sweeps, a
 * routine is a declared strategy, a run is a sweep, a finding is a durable record a
 * sweep may add to or resolve, a proposal is a suggestion waiting on a person), so
 * each gets its own tab and its own list.
 *
 * Above the strip, the breadcrumb `Gardening › {project} › {surface}` is the lens shown in place: its project crumb
 * switches the lens and keeps the surface, and its last choice, All projects, opens the doorway. A project no live
 * runner serves says so here, since nothing its garden raises is worked until one does.
 *
 * Only Proposals carries a waiting count, since only proposals are waiting on
 * somebody — Scopes, routines, runs, and findings accumulate, they do not wait. The
 * count comes off the same proposals read the docket itself reads, so the two never disagree.
 */
@Component({
  selector: 'app-gardening-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    KitCountBadge,
    KitMenu,
    KitMenuItemRadio,
    KitMenuPanel,
    KitTab,
    KitTabStrip,
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
  private readonly proposalsQuery = injectHubGardenProposalsQuery();
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

  /** How many proposals are waiting on a person right now — the strip's own
   * "urgent count" (only Proposals carries one, per the plan). */
  protected readonly proposalsWaitingCount = computed(() => waitingProposalCount(this.proposalsQuery.data() ?? []));

  /** The slug the lens is on — the project crumb's checked choice. */
  protected readonly lensSlug = this.lens.slug;

  /** The project crumb's text: the project's name once read, its slug until then. */
  protected readonly projectName = computed(() => this.project()?.name ?? this.lens.slug() ?? '');

  protected readonly projectChoices = computed(() => gardenProjectChoices(this.projectsQuery.data() ?? []));

  /** The surface crumb: the tab the URL names. */
  protected readonly surface = computed(() => surfaceLabel(gardenPathOf(this.url()).tab));

  protected readonly unserved = computed(() => gardenUnserved(this.project(), this.runnersQuery.data()));

  /** Switches the lens to `slug`, keeping the tab — or opens the doorway for All. */
  protected chooseProject(slug: string | null): void {
    if (slug === this.lens.slug()) return;
    void this.lens.switchTo(slug);
  }
}

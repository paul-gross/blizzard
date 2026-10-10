import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { FLEET_CLOCK, KitAsyncState, KitPanel, asyncStateOf } from 'fleet';
import { LensLink } from 'fleet/shell';

import { injectProjectsQuery } from '../../core/projects.query';
import { injectHubRunnersQuery } from '../../core/runners.query';
import { injectHubRoutinesQuery } from '../core/routines.query';
import { defaultRoutineWindow } from '../core/routine-window';
import { injectHubScopesQuery } from '../core/scopes.query';
import { injectHubGardenProposalsQuery } from '../proposals/garden-proposals.query';
import { injectHubRunsQuery } from '../runs/garden-runs.query';
import { doorwayCards, waitingLabel } from './gardening-doorway.model';

/**
 * The gardening tab on All — a doorway, not a garden. Every scope, routine, run, finding and proposal belongs to one
 * project, so there is no tenant-wide garden to show: one card per project gives its standing at a glance (routines,
 * scopes, recent runs, proposals waiting, and whether any live runner serves it) and opens its garden under its lens.
 *
 * Every count comes from the tenant-wide list reads, grouped by each record's own `project_id`.
 */
@Component({
  selector: 'app-gardening-doorway',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel, LensLink, RouterLink],
  templateUrl: './gardening-doorway.html',
  styleUrl: './gardening-doorway.css',
})
export class GardeningDoorway {
  private readonly window = defaultRoutineWindow(inject(FLEET_CLOCK)());
  private readonly projectsQuery = injectProjectsQuery();
  private readonly scopesQuery = injectHubScopesQuery();
  private readonly routinesQuery = injectHubRoutinesQuery();
  private readonly runsQuery = injectHubRunsQuery(() => this.window.since);
  private readonly proposalsQuery = injectHubGardenProposalsQuery();
  private readonly runnersQuery = injectHubRunnersQuery();

  /** The reporting window the run counts cover. */
  protected readonly windowLabel = this.window.label;

  protected readonly cards = computed(() =>
    doorwayCards({
      projects: this.projectsQuery.data() ?? [],
      scopes: this.scopesQuery.data() ?? [],
      routines: this.routinesQuery.data() ?? [],
      runs: this.runsQuery.data() ?? [],
      proposals: this.proposalsQuery.data() ?? [],
      runners: this.runnersQuery.data(),
    }),
  );

  protected readonly state = computed(() =>
    asyncStateOf(
      [this.projectsQuery, this.scopesQuery, this.routinesQuery, this.runsQuery, this.proposalsQuery],
      this.cards().length === 0,
    ),
  );

  protected readonly waitingLabel = waitingLabel;
}

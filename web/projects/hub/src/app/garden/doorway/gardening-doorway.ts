import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { KitAsyncState, asyncStateOf } from 'fleet';

import { ProjectLens } from '../../core/project-lens';
import { injectProjectDomainsQuery } from '../../core/projects.query';
import { injectHubCadencesQuery } from '../core/cadences.query';
import { GARDENING_DOORWAY_URL, GARDENING_LANDING_TAB } from '../core/garden-project';
import { injectHubRoutinesQuery } from '../core/routines.query';
import { injectHubGardenProposalsQuery } from '../proposals/garden-proposals.query';
import { GardeningDoorwayCards } from './gardening-doorway-cards';
import { doorwayCards } from './gardening-doorway.model';

/** The All-project doorway into a project's garden. */
@Component({
  selector: 'app-gardening-doorway',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GardeningDoorwayCards, KitAsyncState],
  templateUrl: './gardening-doorway.html',
  styleUrl: './gardening-doorway.css',
})
export class GardeningDoorway {
  private readonly lens = inject(ProjectLens);
  private readonly projectsQuery = injectProjectDomainsQuery();
  private readonly cadencesQuery = injectHubCadencesQuery();
  private readonly routinesQuery = injectHubRoutinesQuery();
  private readonly proposalsQuery = injectHubGardenProposalsQuery();

  protected readonly cards = computed(() => doorwayCards(
    this.projectsQuery.data() ?? [],
    this.cadencesQuery.data() ?? [],
    this.routinesQuery.data() ?? [],
    this.proposalsQuery.data() ?? [],
  ));
  protected readonly state = computed(() => asyncStateOf(
    [this.projectsQuery, this.cadencesQuery, this.routinesQuery, this.proposalsQuery],
    this.cards().length === 0,
  ));

  protected chooseProject(slug: string): void {
    void this.lens.switchTo(slug, `${GARDENING_DOORWAY_URL}/${GARDENING_LANDING_TAB}`);
  }
}

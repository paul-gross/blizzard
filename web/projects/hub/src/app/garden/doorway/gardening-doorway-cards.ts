import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { FleetWhen, KitBadge, KitLinkTile } from 'fleet';
import { LensLink } from 'fleet/shell';

import { type DoorwayCard, openGardenLabel, sweepingLabel, waitingLabel } from './gardening-doorway.model';

/**
 * The gardening doorway's cards — one per project, each giving its garden's standing at a glance and the way into it
 * under the project's lens. Presentational: {@link GardeningDoorway} reads and groups the records, this draws the
 * {@link DoorwayCard}s it is handed.
 */
@Component({
  selector: 'app-gardening-doorway-cards',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitBadge, KitLinkTile, LensLink, RouterLink],
  templateUrl: './gardening-doorway-cards.html',
  styleUrl: './gardening-doorway-cards.css',
})
export class GardeningDoorwayCards {
  readonly cards = input.required<readonly DoorwayCard[]>();

  protected readonly waitingLabel = waitingLabel;
  protected readonly sweepingLabel = sweepingLabel;
  protected readonly openGardenLabel = openGardenLabel;
}

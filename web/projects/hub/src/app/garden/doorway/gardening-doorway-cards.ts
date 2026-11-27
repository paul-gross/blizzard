import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitLinkTile, KitSkeleton, KitSwatch } from 'fleet/kit';

import type { DoorwayCard } from './gardening-doorway.model';

/** Compact project choices for the All-project gardening doorway. */
@Component({
  selector: 'app-gardening-doorway-cards',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitLinkTile, KitSkeleton, KitSwatch],
  templateUrl: './gardening-doorway-cards.html',
  styleUrl: './gardening-doorway-cards.css',
})
export class GardeningDoorwayCards {
  readonly cards = input.required<readonly DoorwayCard[]>();
  readonly isLoading = input(false);
  readonly projectSelected = output<string>();

  protected select(event: MouseEvent, slug: string): void {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    this.projectSelected.emit(slug);
  }
}

import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitBadge } from 'fleet';
import type { DeliveryLane } from './delivery-band.model';

/**
 * The Delivery band: one lane per active flow, titled by its repository and branch, showing left to right the chunk
 * landing now, the chunks in line in arrival order, and the last verdict with how long ago it came. A lane with nothing
 * landing and no one in line folds to one line of its verdict.
 *
 * Presentational only: a chunk's click leaves as {@link selectChunk}, a title's as {@link selectFlow}.
 */
@Component({
  selector: 'app-delivery-band-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge, NgTemplateOutlet],
  templateUrl: './delivery-band-view.html',
  styleUrl: './delivery-band-view.css',
})
export class DeliveryBandView {
  readonly lanes = input.required<readonly DeliveryLane[]>();

  readonly selectChunk = output<string>();
  readonly selectFlow = output<string>();
}

import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { formatSeenTag, KitAsyncState, type KitAsyncStateValue, KitSelectRow, KitSkeleton, LaneIcon } from 'fleet';
import { DeliveryChunk } from './delivery-chunk';
import { deliveryWhen, type DeliveryList as DeliveryListValue, type DeliveryRow, type DeliveryWhen, STAGES } from './delivery.model';
import { skeletonRows } from './delivery-skeleton.model';

/**
 * The Delivery list — a row per chunk, the chunks delivering above the chunks delivered — drawn through the kit's
 * async state. Presentational: its rows, its state and the reading clock come in; a row's pick goes out. The rows'
 * dates and ages are read against the clock here, so a tick redraws them without rejoining the list.
 */
@Component({
  selector: 'app-delivery-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DeliveryChunk, KitAsyncState, KitSelectRow, KitSkeleton, LaneIcon, NgTemplateOutlet],
  templateUrl: './delivery-list.html',
  styleUrl: './delivery-list.css',
})
export class DeliveryList {
  readonly state = input.required<KitAsyncStateValue>();
  readonly list = input.required<DeliveryListValue>();
  readonly now = input.required<number>();
  readonly selectedId = input<string | null>(null);
  /** Emitted with the chunk id of the row picked. */
  readonly picked = output<string>();

  protected readonly stages = STAGES;
  /** The loading placeholders, drawn once. */
  protected readonly skeletons = skeletonRows();

  protected when(row: DeliveryRow): DeliveryWhen | null {
    return deliveryWhen(row.at, this.now());
  }

  protected age(at: string): string {
    return formatSeenTag(at, this.now());
  }
}

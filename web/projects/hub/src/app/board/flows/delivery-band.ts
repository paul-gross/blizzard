import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { injectNowSignal, type WorkItemsView } from 'fleet';
import { DeliveryBandView } from './delivery-band-view';
import { deliveryLanes } from './delivery-band.model';
import { injectActiveFlowsQuery } from './flows.query';

/**
 * The board's Delivery band **container**: reads the tenant's active flows and renders one lane per flow above the
 * status columns through {@link DeliveryBandView}. With no active flow — or before the read answers — it renders
 * nothing, so the board takes the room.
 */
@Component({
  selector: 'app-delivery-band',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DeliveryBandView],
  templateUrl: './delivery-band.html',
  styleUrl: './delivery-band.css',
})
export class DeliveryBand {
  /** The board's work items by chunk id, which a lane's chunks take their titles from. */
  readonly workItems = input<ReadonlyMap<string, WorkItemsView>>(new Map());

  /** Emitted with a chunk id when a lane's chunk is clicked. */
  readonly selectChunk = output<string>();

  /** Emitted with a flow id when a lane's title is clicked. */
  readonly selectFlow = output<string>();

  private readonly flowsQuery = injectActiveFlowsQuery();
  private readonly now = injectNowSignal();

  protected readonly lanes = computed(() => deliveryLanes(this.flowsQuery.data() ?? [], this.workItems(), this.now()));
}

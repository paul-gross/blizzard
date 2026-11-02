import { ChangeDetectionStrategy, Component, computed } from '@angular/core';

import { injectChunkUrlSelection } from 'fleet';
import { injectDeliveryData } from './delivery-data';
import { DeliveryDrawer } from './delivery-drawer';
import { DeliveryList } from './delivery-list';
import { findRow, toggledSelection } from './delivery.model';
import { injectScrollSelectionIntoView } from '../core/scroll-selection-into-view';

/**
 * The `/delivery` route — the desktop Delivery tab: the {@link DeliveryList} over a drawer for the row picked. Reads
 * the tenant's active flows and their newest record facts, and the board's chunk list, work items and runner hosts;
 * {@link injectDeliveryData} joins them. The pick is held in the URL's `?chunk=`, as the board's dock holds its own;
 * picking the picked row again, or Escape, clears it.
 */
@Component({
  selector: 'app-delivery-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DeliveryDrawer, DeliveryList],
  templateUrl: './delivery-page.html',
  styleUrl: './delivery-page.css',
  host: { '(document:keydown.escape)': 'clear()' },
})
export class DeliveryPage {
  private readonly data = injectDeliveryData();
  private readonly selection = injectChunkUrlSelection();

  protected readonly repositories = this.data.repositories;
  protected readonly flows = this.data.flows;
  protected readonly state = this.data.state;
  protected readonly list = this.data.list;
  protected readonly now = this.data.now;
  protected readonly selectedId = this.selection.chunkId;

  constructor() {
    // The picked row stays in view as the drawer opens.
    injectScrollSelectionIntoView(this.selectedId, (id) => `[data-testid="delivery-row"][data-chunk="${id}"]`);
  }

  /** The picked row, while the list holds it. */
  protected readonly selectedRow = computed(() => findRow(this.list(), this.selectedId()));

  protected pick(chunkId: string): void {
    this.selection.select(toggledSelection(this.selectedId(), chunkId));
  }

  protected clear(): void {
    if (this.selectedId() !== null) this.selection.select(null);
  }
}

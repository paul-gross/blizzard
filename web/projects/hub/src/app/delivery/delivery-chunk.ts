import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import { foldTitles, KitSkeleton } from 'fleet';
import type { DeliveryChunkRef } from './delivery.model';

/**
 * A chunk as a Delivery row names it — each work item's ref over its title, as the board's list rows do, a long group
 * folded behind a disclosure. A chunk with no work refs names its short id alone.
 */
@Component({
  selector: 'app-delivery-chunk',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitSkeleton],
  templateUrl: './delivery-chunk.html',
  styleUrl: './delivery-chunk.css',
})
export class DeliveryChunk {
  readonly ref = input.required<DeliveryChunkRef>();

  protected readonly expanded = signal(false);

  /** The items shown, and how many the fold hides. */
  protected readonly fold = computed(() => foldTitles(this.ref().lines, this.expanded()));

  protected toggleFold(): void {
    this.expanded.update((open) => !open);
  }
}

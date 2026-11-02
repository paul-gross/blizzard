import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { type FlowView, injectHubChunkDetailQuery, injectNowSignal, KitSkeleton, type RepositorySummary, toneColor } from 'fleet';
import { LensLink } from 'fleet/shell';
import { deliveryDrawerView, type DrawerStep, outputTail } from './delivery-history.model';
import { STAGES, type DeliveryRow } from './delivery.model';

/**
 * The Delivery page's bottom drawer for the picked row: the chunk's header, a block per repository it delivers to,
 * and its delivery history newest first — read from the chunk's own detail, which the board's dock shares.
 */
@Component({
  selector: 'app-delivery-drawer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitSkeleton, LensLink, RouterLink],
  templateUrl: './delivery-drawer.html',
  styleUrl: './delivery-drawer.css',
})
export class DeliveryDrawer {
  readonly row = input.required<DeliveryRow>();
  readonly flows = input.required<readonly FlowView[]>();
  readonly repositories = input.required<readonly RepositorySummary[]>();

  private readonly detailQuery = injectHubChunkDetailQuery(() => this.row().chunk.chunkId);
  private readonly now = injectNowSignal();

  protected readonly stages = STAGES;
  protected readonly toneColor = toneColor;
  protected readonly pending = computed(() => this.detailQuery.isPending());
  protected readonly failed = computed(() => this.detailQuery.isError());
  protected readonly view = computed(() => deliveryDrawerView(this.detailQuery.data(), this.flows(), this.repositories(), this.now()));

  /** The history entries whose step output is unfolded. */
  private readonly unfolded = signal<ReadonlySet<string>>(new Set());

  protected isUnfolded(id: string): boolean {
    return this.unfolded().has(id);
  }

  protected tail(id: string, step: DrawerStep): string {
    return outputTail(step, this.isUnfolded(id));
  }

  protected toggle(id: string): void {
    this.unfolded.update((held) => {
      const next = new Set(held);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }
}

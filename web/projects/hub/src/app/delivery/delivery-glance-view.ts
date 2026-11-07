import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { formatSeenTag, KitAsyncState, type KitAsyncStateValue, type KitCountCell, KitCountStrip, KitSkeleton, LaneIcon } from 'fleet';
import { LensLink } from 'fleet/shell';
import { cardStageColor, cardStageLabel, deliveredRecently, type DeliveryList, type DeliveryRow } from './delivery.model';

/** The phone list's two groups. */
type DeliveryFilter = 'delivering' | 'recent';

/**
 * The phone's Delivery list as compact cards — the chunks delivering above the chunks delivered in the board's done
 * window, under a strip of their counts that narrows the list to one. Presentational: the list, its read state and
 * the reading clock come in; a card links to the chunk's own delivery screen (`/delivery/chunk/:chunkId`).
 */
@Component({
  selector: 'app-delivery-glance-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitCountStrip, KitSkeleton, LaneIcon, LensLink, NgTemplateOutlet, RouterLink],
  templateUrl: './delivery-glance-view.html',
  styleUrl: './delivery-glance-view.css',
})
export class DeliveryGlanceView {
  readonly state = input.required<KitAsyncStateValue>();
  readonly list = input.required<DeliveryList>();
  readonly now = input.required<number>();

  /** The delivered rows delivered recently ({@link deliveredRecently}). */
  protected readonly recent = computed(() => deliveredRecently(this.list(), this.now()));

  /** The group the list is narrowed to, or `null` for both. A tile toggles, as the board glance's vitals do. */
  protected readonly filter = signal<DeliveryFilter | null>(null);

  /** The filter strip's two tiles: each group's count over its caption. */
  protected readonly filterCells = computed<readonly KitCountCell[]>(() => [
    { key: 'delivering', count: this.list().delivering.length, caption: 'delivering', testid: 'delivery-filter-delivering', selectable: true },
    { key: 'recent', count: this.recent().length, caption: 'delivered 48h', testid: 'delivery-filter-recent', selectable: true },
  ]);

  /** Takes a {@link filterCells} key, so always a {@link DeliveryFilter}. */
  protected toggle(key: string): void {
    const filter = key as DeliveryFilter;
    this.filter.update((current) => (current === filter ? null : filter));
  }

  protected readonly stageLabel = cardStageLabel;
  protected readonly stageColor = cardStageColor;

  protected age(row: DeliveryRow): string {
    return row.at ? formatSeenTag(row.at, this.now()) : '';
  }
}

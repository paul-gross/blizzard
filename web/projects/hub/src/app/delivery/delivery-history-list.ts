import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { KitSkeleton, toneColor } from 'fleet/kit';
import { AppLink } from 'fleet/shell';
import type { HistoryEntry } from './delivery-history.model';

/**
 * A chunk's delivery history, newest first, as one grid of rows: the stamp, the description, and the age. While
 * `loading` it draws the same rows as bars on their real layout. Presentational.
 */
@Component({
  selector: 'app-delivery-history-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitSkeleton, AppLink, RouterLink],
  templateUrl: './delivery-history-list.html',
  styleUrl: './delivery-history-list.css',
})
export class DeliveryHistoryList {
  readonly entries = input<readonly HistoryEntry[]>([]);
  /** The chunk the history belongs to — the base of each entry's log link. */
  readonly chunkId = input('');
  readonly loading = input(false);

  protected readonly toneColor = toneColor;
  /** The loading skeleton's bar widths, in percent. */
  protected readonly skHistory = [62, 48, 70, 40, 56, 66];
}

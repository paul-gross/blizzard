import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { type ChunkDetailAggregate, KitAsyncState, type KitAsyncStateValue, KitCloseButton, KitSectionHead, KitSkeleton, toneColor } from 'fleet';
import { ChunkDetailHeader } from '../core/chunk-detail-header/chunk-detail-header';
import { DeliveryHistoryList } from './delivery-history-list';
import type { DeliveryDrawerView as DrawerViewValue } from './delivery-history.model';

/**
 * The drawer's face for one chunk: the board dock's chunk header (without its actions), a block per repository it
 * delivers to, and its delivery history newest first, drawn through the kit's async state. Presentational: the
 * detail, its joined view and its read state come in; the close goes out.
 */
@Component({
  selector: 'app-delivery-drawer-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkDetailHeader, KitAsyncState, KitCloseButton, KitSectionHead, KitSkeleton, DeliveryHistoryList],
  templateUrl: './delivery-drawer-view.html',
  styleUrl: './delivery-drawer-view.css',
})
export class DeliveryDrawerView {
  readonly chunkId = input.required<string>();
  readonly state = input.required<KitAsyncStateValue>();
  /** The chunk's detail, `undefined` while unread. */
  readonly detail = input<ChunkDetailAggregate | undefined>(undefined);
  /** The drawer's joined view of {@link detail}, `null` while unread. */
  readonly view = input<DrawerViewValue | null>(null);
  readonly projectColor = input<string | null>(null);
  readonly runnerHosts = input<ReadonlyMap<string, string>>(new Map());
  /** Whether the drawer draws its close button — off on the phone's screen, whose back row returns to the list. */
  readonly closable = input(true);
  /** Emitted when the operator closes the drawer. */
  readonly dismiss = output<void>();

  protected readonly toneColor = toneColor;
  /** The loading skeleton's bar widths, in percent: two repository cards. */
  protected readonly skRepos = [70, 55];
}

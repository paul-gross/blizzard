import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';

import {
  KitAsyncState,
  KitBadge,
  KitPanel,
  KitSkeleton,
  STATUS_TONE,
  projectTint,
  toneColor,
  type ChunkStatus,
  formatCost,
  formatTokens,
  hasCostFigure,
  type FleetSpendView,
  type KitAsyncStateValue,
} from 'fleet';
import { LensLink } from 'fleet/shell';

import type { WorkLine } from '../board-card/board-card.model';
import { LaneIcon } from '../lane-icon/lane-icon';
import type { GlanceListGroups } from './glance-board.model';

/** What every chunk row leads with: the chunk's work items, each its work-ref label over its title. */
export interface RowWork {
  readonly lines: readonly WorkLine[];
  /** Whether the chunk's work items have been read — until then each title holds a skeleton. */
  readonly itemsRead: boolean;
}

/** The vitals strip's numbers — "am I needed?", answered before a single scroll. */
export interface Vitals {
  readonly needsYou: number;
  readonly running: number;
  readonly ready: number;
  readonly doneToday: number;
  /** `{online}/{total}` across the fleet registry. */
  readonly runnersUpLabel: string;
}

/**
 * The mobile glance board's presentational half (mock screen C,
 * `../docs/designs/mobile/core-flows.html`) — one scrolling column: the vitals strip
 * and the day's spend ("Fleet spend · today"), then every chunk as one list of cards,
 * read the way the desktop list reads its rows — attention and work in flight, READY
 * over a divider over BACKLOG, then done today.
 *
 * Presentational (`bzh:frontend-container-presentational`): renders exactly what
 * it is handed and injects no query. Each chunk row carries a whole-row
 * `routerLink` into that chunk's detail page (`/board/chunk/:chunkId`) — the one
 * navigation this view owns, expressed as a link rather than an output so a row
 * behaves like a link should (long-press, open-in-new-tab, a real `href`).
 */
@Component({
  selector: 'app-glance-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitPanel, KitSkeleton, LaneIcon, NgTemplateOutlet, RouterLink, LensLink],
  templateUrl: './glance-view.html',
  styleUrl: './glance-view.css',
})
export class GlanceView {
  protected readonly formatCost = formatCost;
  protected readonly projectTint = projectTint;
  protected readonly hasCostFigure = hasCostFigure;
  protected readonly formatTokens = formatTokens;

  readonly vitals = input.required<Vitals>();
  readonly list = input.required<GlanceListGroups>();
  readonly listState = input.required<KitAsyncStateValue>();
  readonly spend = input<FleetSpendView | null>(null);
  readonly spendState = input.required<KitAsyncStateValue>();

  /** A chunk's status tone as a color — the card's left edge, as a desktop list row's. */
  protected statusColor(status: ChunkStatus): string {
    return toneColor(STATUS_TONE(status));
  }

  /** A spend total's full token count — every class summed
   * (`ChunkUsageTotalView`/`FleetSpendView` both carry the same four fields),
   * matching `chunk-token-breakdown.ts`'s own fold. */
  protected totalTokens(s: FleetSpendView): number {
    return s.input_tokens + s.output_tokens + s.cache_read_tokens + s.cache_create_tokens;
  }
}

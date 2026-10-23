import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';

import {
  KitAsyncState,
  KitPanel,
  KitSkeleton,
  formatCost,
  formatTokens,
  hasCostFigure,
  type FleetSpendView,
  type KitAsyncStateValue,
} from 'fleet';
import { LensLink } from 'fleet/shell';

import type { WorkLine } from '../board-card/board-card.model';

/** What every chunk row leads with: the chunk's work items, each its work-ref label over its title. */
export interface RowWork {
  readonly lines: readonly WorkLine[];
  /** Whether the chunk's work items have been read — until then each title holds a skeleton. */
  readonly itemsRead: boolean;
}

/** One "Needs you" row — an open ask (the more specific reason), an open gate,
 * or a chunk whose derived tone is `waiting`/`needs` with neither. */
export interface AttentionRow extends RowWork {
  readonly chunkId: string;
  readonly shortId: string;
  /** What the panel's heading does not already say — `gate` for an open decision, or a
   * parked chunk's own status (`paused`) — else `null`. */
  readonly status: string | null;
  /** The ask's own question text, the gate's node name, or a fallback naming
   * where the chunk sits. */
  readonly sub: string;
}

/** One "In motion" row — a chunk whose tone is `running` (running or delivering). */
export interface MotionRow extends RowWork {
  readonly chunkId: string;
  readonly shortId: string;
  readonly node: string;
  /** `delivering` for a delivering chunk; `null` for a running one, which the panel already names. */
  readonly status: string | null;
  readonly costUsd: number;
  readonly costPartial: boolean;
  /** The chunk's derived spend estimate — `null` iff no summed row reported one. */
  readonly estimatedCostUsd: number | null;
}

/** One "Up next" row — a currently READY chunk in the hub's dispatch order. */
export interface UpNextRow extends RowWork {
  readonly chunkId: string;
  readonly shortId: string;
  readonly node: string;
}

/** One "Done today" row — a terminal chunk completed in the rolling previous 24 hours. */
export interface DoneRow extends RowWork {
  readonly chunkId: string;
  readonly shortId: string;
  /** `stopped` for a stopped chunk; `null` for a done one, which the panel already names. */
  readonly status: string | null;
}

/** The vitals strip's four numbers — "am I needed?", answered before a single scroll. */
export interface Vitals {
  readonly needsYou: number;
  readonly running: number;
  /** `{online}/{total}` across the fleet registry. */
  readonly runnersUpLabel: string;
  /** Whether the SSE spine is connected — drives the pulsing live dot. */
  readonly live: boolean;
  readonly liveLabel: string;
}

/**
 * The mobile glance board's presentational half (mock screen C,
 * `../docs/designs/mobile/core-flows.html`) — one scrolling column, ordered by
 * attention rather than by entity: the vitals strip and the day's spend ("Fleet
 * spend · today"), then "Needs you", "In motion", "Up next", and "Done today".
 * Each chunk row leads with its work items, as a board card does, and names its status
 * only where the panel's heading does not.
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
  imports: [KitAsyncState, KitPanel, KitSkeleton, NgTemplateOutlet, RouterLink, LensLink],
  templateUrl: './glance-view.html',
  styleUrl: './glance-view.css',
})
export class GlanceView {
  protected readonly formatCost = formatCost;
  protected readonly hasCostFigure = hasCostFigure;
  protected readonly formatTokens = formatTokens;

  readonly vitals = input.required<Vitals>();
  readonly needsYou = input<readonly AttentionRow[]>([]);
  readonly needsYouState = input.required<KitAsyncStateValue>();
  readonly inMotion = input<readonly MotionRow[]>([]);
  readonly inMotionState = input.required<KitAsyncStateValue>();
  readonly upNext = input<readonly UpNextRow[]>([]);
  readonly upNextState = input.required<KitAsyncStateValue>();
  readonly doneToday = input<readonly DoneRow[]>([]);
  readonly doneTodayState = input.required<KitAsyncStateValue>();
  /** The denominator in Done today's visible/total terminal header count. */
  readonly doneTodayTotal = input<number | null>(0);
  readonly spend = input<FleetSpendView | null>(null);
  readonly spendState = input.required<KitAsyncStateValue>();

  /** A spend total's full token count — every class summed
   * (`ChunkUsageTotalView`/`FleetSpendView` both carry the same four fields),
   * matching `chunk-token-breakdown.ts`'s own fold. */
  protected totalTokens(s: FleetSpendView): number {
    return s.input_tokens + s.output_tokens + s.cache_read_tokens + s.cache_create_tokens;
  }
}

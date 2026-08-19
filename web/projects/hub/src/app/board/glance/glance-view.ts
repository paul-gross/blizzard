import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import {
  KitAsyncState,
  KitBadge,
  KitPanel,
  KitSkeleton,
  formatCost,
  formatTokens,
  type FleetSpendView,
  type KitAsyncStateValue,
  type Tone,
} from 'fleet';

/** One "Needs you" row — an open ask (the more specific reason) or a chunk
 * whose derived tone is `waiting`/`needs` with no open ask of its own. */
export interface AttentionRow {
  readonly chunkId: string;
  readonly shortId: string;
  readonly runnerId: string | null;
  /** The row's badge color — `chunk-lanes.ts`'s `STATUS_TONE` vocabulary
   * (`waiting` or `needs`), never a parallel color table. */
  readonly tone: Tone;
  /** The badge's text — `ask` for an open question, `needs human` for an
   * escalation, `waiting` for a parked (e.g. paused) chunk with no open ask. */
  readonly pillLabel: string;
  /** The ask's own question text, or a fallback naming where the chunk sits. */
  readonly sub: string;
}

/** One "In motion" row — a chunk whose tone is `running` (running or delivering). */
export interface MotionRow {
  readonly chunkId: string;
  readonly shortId: string;
  readonly runnerId: string | null;
  readonly node: string;
  readonly pillLabel: 'run' | 'deliver';
  readonly costUsd: number;
  readonly costPartial: boolean;
}

/** One "Done today" row — a chunk whose tone is `done` (stopped or done). */
export interface DoneRow {
  readonly chunkId: string;
  readonly shortId: string;
  /** The chunk's work item label, empty when no pointer names a configured source. */
  readonly pointerLabel: string;
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
 * attention rather than by entity: the vitals strip, then "Needs you", "In
 * motion", "Done today", and the comfort numbers ("Fleet spend · today") last.
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
  imports: [KitAsyncState, KitBadge, KitPanel, KitSkeleton, RouterLink],
  templateUrl: './glance-view.html',
  styleUrl: './glance-view.css',
})
export class GlanceView {
  protected readonly formatCost = formatCost;
  protected readonly formatTokens = formatTokens;

  readonly vitals = input.required<Vitals>();
  readonly needsYou = input<readonly AttentionRow[]>([]);
  readonly needsYouState = input.required<KitAsyncStateValue>();
  readonly inMotion = input<readonly MotionRow[]>([]);
  readonly inMotionState = input.required<KitAsyncStateValue>();
  readonly doneToday = input<readonly DoneRow[]>([]);
  readonly doneTodayState = input.required<KitAsyncStateValue>();
  readonly spend = input<FleetSpendView | null>(null);
  readonly spendState = input.required<KitAsyncStateValue>();

  /** A spend total's full token count — every class summed (issue #59's
   * `ChunkUsageTotalView`/`FleetSpendView` both carry the same four fields),
   * matching `chunk-token-breakdown.ts`'s own fold. */
  protected totalTokens(s: FleetSpendView): number {
    return s.input_tokens + s.output_tokens + s.cache_read_tokens + s.cache_create_tokens;
  }
}

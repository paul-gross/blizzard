import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';

import {
  DONE_LANE,
  FleetWhen,
  KitAsyncState,
  KitBadge,
  KitPanel,
  KitSkeleton,
  STATUS_TONE,
  projectTint,
  laneTone,
  toneColor,
  type ChunkStatus,
  formatCost,
  formatSeenTag,
  injectNowSignal,
  hasCostFigure,
  type FleetSpendView,
  type KitAsyncStateValue,
  type KitCountCell,
  KitCountStrip,
  type WorkLine,
  LaneIcon,
} from 'fleet';
import { LensLink } from 'fleet/shell';

import { FleetSpend } from '../fleet-spend/fleet-spend';
import { NodeStrip } from '../node-strip/node-strip';
import { showsNodeStrip } from '../node-strip/node-strip.model';
import { boardDonePolicy, type BoardDonePolicy } from '../../core/board-done-policy.model';
import { graphStepLabel } from '../../core/step-label';
import { filterGlanceRows, type GlanceFilter, type GlanceListGroups, type GlanceRow } from './glance-board.model';

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
  readonly done: number;
  /** `{online}/{total}` across the fleet registry. */
  readonly runnersUpLabel: string;
}

/**
 * The mobile glance board's presentational half (mock screen C,
 * `../docs/designs/mobile/core-flows.html`) — one scrolling column: the vitals strip
 * and the day's spend ("Fleet spend · today"), then every chunk as one list of cards,
 * read the way the desktop list reads its rows — attention and work in flight, READY
 * over a divider over BACKLOG, then done.
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
  imports: [FleetSpend, FleetWhen, NodeStrip, KitAsyncState, KitBadge, KitCountStrip, KitPanel, KitSkeleton, LaneIcon, NgTemplateOutlet, RouterLink, LensLink],
  templateUrl: './glance-view.html',
  styleUrl: './glance-view.css',
})
export class GlanceView {
  private readonly now = injectNowSignal();

  /** A done row's bracketed age since completion, as the Delivery glance tags its rows. */
  protected doneAge(completedAt: string): string {
    return formatSeenTag(completedAt, this.now());
  }

  protected readonly formatCost = formatCost;
  protected readonly projectTint = projectTint;
  protected readonly hasCostFigure = hasCostFigure;
  protected readonly showsNodeStrip = showsNodeStrip;

  /** A card's step, named with its graph when the hub names it (`adv-dwf/pre-push`). */
  protected stepLabel(row: GlanceRow): string {
    return graphStepLabel(row.graphName, row.node);
  }

  readonly donePolicy = input<BoardDonePolicy>(boardDonePolicy([]));
  readonly vitals = input.required<Vitals>();
  readonly list = input.required<GlanceListGroups>();
  readonly listState = input.required<KitAsyncStateValue>();
  readonly spend = input<FleetSpendView | null>(null);
  readonly spendState = input.required<KitAsyncStateValue>();

  /** The fleet-wide spend over yesterday, `null` until it resolves — its row is withheld until then. */
  readonly spendYesterday = input<FleetSpendView | null>(null);


  protected readonly DONE_LANE = DONE_LANE;

  /** A vital's color — the tone of the board lane it counts. Needs-you counts the NEEDS HUMAN
   * lane and lights only while non-zero. */
  protected laneColor(key: string): string {
    return toneColor(laneTone(key));
  }

  /** The vital the list is narrowed to — tapping a count shows only its cards; tapping it again clears it. */
  protected readonly filter = signal<GlanceFilter | null>(null);

  /** The vitals strip: four lane counts that each narrow the list, then the runners-up readout. */
  protected readonly vitalCells = computed<readonly KitCountCell[]>(() => {
    const v = this.vitals();
    return [
      { key: 'needs', count: v.needsYou, caption: 'needs you', testid: 'vital-needs-you', selectable: true, countColor: v.needsYou > 0 ? this.laneColor('needs') : null },
      { key: 'running', count: v.running, caption: 'running', testid: 'vital-running', selectable: true, countColor: this.laneColor('running') },
      { key: 'ready', count: v.ready, caption: 'ready', testid: 'vital-ready', selectable: true },
      { key: 'done', count: v.done, caption: this.donePolicy().heading, testid: 'vital-done-today', selectable: true, countColor: this.laneColor(DONE_LANE) },
      { key: 'runners', count: v.runnersUpLabel, caption: 'runners up', testid: 'vital-runners-up', selectable: false },
    ];
  });

  /** The list's cards under {@link filter}, in the order the unfiltered list shows them. */
  protected readonly filtered = computed(() => filterGlanceRows(this.list(), this.filter()));

  /** Takes a selectable {@link vitalCells} key, so always a {@link GlanceFilter}. */
  protected toggleFilter(key: string): void {
    const filter = key as GlanceFilter;
    this.filter.update((current) => (current === filter ? null : filter));
  }

  /** A chunk's status tone as a color — the card's left edge, as a desktop list row's. */
  protected statusColor(status: ChunkStatus): string {
    return toneColor(STATUS_TONE(status));
  }
}

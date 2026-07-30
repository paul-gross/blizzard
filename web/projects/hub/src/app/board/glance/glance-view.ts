import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import {
  KitAsyncState,
  KitBadge,
  KitPanel,
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
  imports: [KitAsyncState, KitBadge, KitPanel, RouterLink],
  template: `
    <div class="glance" data-testid="glance-board">
      <div class="vitals" data-testid="glance-vitals">
        <div class="vital" data-testid="vital-needs-you">
          <span class="v-n" [class.hot]="vitals().needsYou > 0">{{ vitals().needsYou }}</span>
          <span class="v-cap">needs you</span>
        </div>
        <div class="vital" data-testid="vital-running">
          <span class="v-n live">{{ vitals().running }}</span>
          <span class="v-cap">running</span>
        </div>
        <div class="vital" data-testid="vital-runners-up">
          <span class="v-n">{{ vitals().runnersUpLabel }}</span>
          <span class="v-cap">runners up</span>
        </div>
        <div class="vital" data-testid="vital-live">
          <span class="live-dot" [class.active]="vitals().live" [attr.title]="vitals().liveLabel"></span>
          <span class="v-cap">{{ vitals().liveLabel }}</span>
        </div>
      </div>

      <fleet-kit-panel
        aria-label="Needs you"
        data-testid="needs-you-panel"
        label="Needs you"
        accent="var(--red)"
        [count]="needsYou().length || null"
        countTestid="needs-you-count"
      >
        <fleet-kit-async-state
          [state]="needsYouState()"
          loadingText="LOADING…"
          loadingTestid="needs-you-loading"
          errorText="FAILED TO LOAD"
          errorTestid="needs-you-error"
          emptyText="NOTHING NEEDS YOU"
          emptyTestid="needs-you-empty"
        >
          <ul class="rows">
            @for (row of needsYou(); track row.chunkId) {
              <li class="row" data-testid="needs-you-row" [attr.data-chunk]="row.chunkId">
                <a class="hit" [routerLink]="['/board', 'chunk', row.chunkId]" [attr.aria-label]="'Open chunk ' + row.shortId"></a>
                <fleet-kit-badge class="pill" [tone]="row.tone" variant="soft">{{ row.pillLabel }}</fleet-kit-badge>
                <span class="grow">
                  <span class="cid">{{ row.shortId }}</span>
                  @if (row.runnerId) {
                    <span class="rid">{{ row.runnerId }}</span>
                  }
                  <span class="sub">{{ row.sub }}</span>
                </span>
              </li>
            }
          </ul>
        </fleet-kit-async-state>
      </fleet-kit-panel>

      <fleet-kit-panel
        aria-label="In motion"
        data-testid="in-motion-panel"
        label="In motion"
        accent="var(--cyan)"
        [count]="inMotion().length || null"
        countTestid="in-motion-count"
      >
        <fleet-kit-async-state
          [state]="inMotionState()"
          loadingText="LOADING…"
          loadingTestid="in-motion-loading"
          errorText="FAILED TO LOAD"
          errorTestid="in-motion-error"
          emptyText="NOTHING IN MOTION"
          emptyTestid="in-motion-empty"
        >
          <ul class="rows">
            @for (row of inMotion(); track row.chunkId) {
              <li class="row" data-testid="in-motion-row" [attr.data-chunk]="row.chunkId">
                <a class="hit" [routerLink]="['/board', 'chunk', row.chunkId]" [attr.aria-label]="'Open chunk ' + row.shortId"></a>
                <fleet-kit-badge class="pill" tone="running" variant="soft">{{ row.pillLabel }}</fleet-kit-badge>
                <span class="grow">
                  <span class="cid">{{ row.shortId }}</span>
                  <span class="rid">{{ row.runnerId }} · {{ row.node }}</span>
                </span>
                @if (row.costUsd > 0 || row.costPartial) {
                  <span class="num" data-testid="in-motion-cost">{{ formatCost(row.costUsd, row.costPartial) }}</span>
                }
              </li>
            }
          </ul>
        </fleet-kit-async-state>
      </fleet-kit-panel>

      <fleet-kit-panel
        aria-label="Done today"
        data-testid="done-today-panel"
        label="Done today"
        accent="var(--green)"
        [count]="doneToday().length || null"
        countTestid="done-today-count"
      >
        <fleet-kit-async-state
          [state]="doneTodayState()"
          loadingText="LOADING…"
          loadingTestid="done-today-loading"
          errorText="FAILED TO LOAD"
          errorTestid="done-today-error"
          emptyText="NOTHING DONE YET"
          emptyTestid="done-today-empty"
        >
          <ul class="rows">
            @for (row of doneToday(); track row.chunkId) {
              <li class="row" data-testid="done-today-row" [attr.data-chunk]="row.chunkId">
                <a class="hit" [routerLink]="['/board', 'chunk', row.chunkId]" [attr.aria-label]="'Open chunk ' + row.shortId"></a>
                <fleet-kit-badge class="pill" tone="done" variant="soft">done</fleet-kit-badge>
                <span class="grow">
                  <span class="cid">{{ row.shortId }}</span>
                  @if (row.pointerLabel) {
                    <span class="rid">{{ row.pointerLabel }}</span>
                  }
                </span>
              </li>
            }
          </ul>
        </fleet-kit-async-state>
      </fleet-kit-panel>

      <fleet-kit-panel aria-label="Fleet spend today" data-testid="glance-spend-panel" label="Fleet spend · today">
        <fleet-kit-async-state
          [state]="spendState()"
          loadingText="—"
          loadingTestid="glance-spend-loading"
          errorText="FAILED TO LOAD"
          errorTestid="glance-spend-error"
        >
          @if (spend(); as s) {
            <div class="row" data-testid="glance-spend-row">
              <span class="cid">{{ formatCost(s.cost_usd, s.cost_partial) }}</span>
              <span class="sub">{{ formatTokens(totalTokens(s)) }} tok</span>
            </div>
          } @else {
            <p class="empty" data-testid="glance-spend-empty">—</p>
          }
        </fleet-kit-async-state>
      </fleet-kit-panel>
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      min-height: 0;
      overflow-y: auto;
      color: var(--text);
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
    }
    .glance {
      display: flex;
      flex-direction: column;
      gap: 6px;
      padding: 6px;
    }
    /* The vitals strip: three counts plus the live indicator, answering "am I
       needed?" before the rest of the column ever has to scroll into view. */
    .vitals {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      border: 1px solid var(--bezel);
      background: var(--panel-deep);
    }
    .vital {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 2px;
      padding: 10px 6px;
      border-right: 1px solid var(--line);
    }
    .vital:last-child {
      border-right: 0;
    }
    .v-n {
      font-size: var(--fs-lg);
      color: var(--snow);
    }
    .v-n.hot {
      color: var(--red);
    }
    .v-n.live {
      color: var(--cyan);
    }
    .v-cap {
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--label);
    }
    .live-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--label-dim);
    }
    .live-dot.active {
      background: var(--cyan);
      box-shadow: 0 0 8px var(--cyan);
    }
    @media (prefers-reduced-motion: no-preference) {
      .live-dot.active {
        animation: glance-live-pulse 2.2s ease-in-out infinite;
      }
    }
    @keyframes glance-live-pulse {
      50% {
        opacity: 0.35;
      }
    }
    .empty {
      color: var(--label-dim);
      padding: 10px 8px;
      margin: 0;
      font-size: var(--fs-sm);
      letter-spacing: 0.08em;
    }
    .rows {
      list-style: none;
      margin: 0;
      padding: 0;
    }
    .row {
      position: relative;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px;
      border-bottom: 1px solid var(--line);
    }
    .row:last-child {
      border-bottom: 0;
    }
    /* A whole-row hit target for the chunk drill-down, laid over the row rather
       than wrapped around it — the row's cells stay a flex layout of spans and
       badges. pointer-events:none inherits, so any control added inside a cell
       later needs pointer-events:auto of its own to stay clickable; none of
       these rows carries one today. Scoped to :has(.hit) so the spend row —
       same .row class, no drill-down — keeps normal behavior and never flashes
       a pressed state for a tap that goes nowhere. */
    .hit {
      position: absolute;
      inset: 0;
      z-index: 0;
    }
    .row:has(.hit) > *:not(.hit) {
      position: relative;
      z-index: 1;
      pointer-events: none;
    }
    .row:has(.hit):active {
      background: var(--bezel-hi);
    }
    .grow {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 6px;
    }
    .cid {
      color: var(--amber-hi);
      font-size: var(--fs-sm);
    }
    .rid {
      color: var(--label);
      font-size: var(--fs-xs);
      white-space: nowrap;
    }
    .sub {
      flex-basis: 100%;
      color: var(--text);
      font-size: var(--fs-xs);
      overflow-wrap: anywhere;
    }
    .num {
      color: var(--amber-hi);
      font-size: var(--fs-xs);
      white-space: nowrap;
      text-align: right;
    }
  `,
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

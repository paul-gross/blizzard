import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { ChunkSummary, FleetSpendView } from '../api/hub';
import { LANES, laneFor } from '../chunk-lanes';
import { BrandMark } from '../design/brand-mark';
import { formatCost } from '../cost-format';

/** One header stat cell — a label over its live count, optionally shown as a
 * `value/capacity` fraction (e.g. the runner's `envs 2/4`). `capacity` is
 * omitted for the hub's plain per-lane counts. */
export interface StatCell {
  readonly key: string;
  readonly label: string;
  readonly value: number;
  readonly capacity?: number;
}

/**
 * The mission-control titlebar — the brand, a fleet's live counts, and a
 * connection state. Shared by the hub board (its own chunk-derived lane counts,
 * via {@link chunks}) and the runner's local panel (its own capacity cells, via
 * {@link stats}) — one header, one 48px chrome, rather than each app rendering its
 * own bespoke bar (issue #131). It spans the whole window above whatever it sits
 * over, so it lives here rather than inside a routed page: a header nested in a
 * content column would only span that column, leaving the rails to start above it.
 *
 * Presentational only: every cell is derived from plain inputs, never an
 * injected query. `stats`, given, renders in place of the chunk-derived lane
 * cells — the runner has no chunk list, so it supplies its own. The trailing
 * `[header-trailing]`-selected content projection is the composable slot future
 * header controls (an avatar menu, a pause toggle) slot into without this
 * component knowing about either. All color comes from the design-token layer,
 * never hard-coded hex.
 *
 * Collapses in tiers as it narrows (issue #163), driven by `@container`
 * queries on the header's own inline size rather than the viewport's: the two
 * shells mount it over different layouts, so each must react to the width it
 * actually has. Below ~1150px the stat strip drops; below ~700px the spend cell
 * and the brand text (wordmark and tagline both) follow, leaving the brand mark.
 * The connection cell is `flex: none` at every width and the trailing cluster
 * shrinks only into content its consumer marked shrinkable, so neither can be
 * pushed past the clipped right edge of an `overflow: hidden` shell; the stat
 * strip is the only region this component shrinks on its own.
 *
 * That guarantee only covers what *this* component owns. A consumer projecting
 * more than a menu into `[header-trailing]` — the runner's local panel projects
 * a pause control and an identity block beside its menu — owns both collapsing
 * its own controls and deciding which of them truncates, and can do both: the
 * query container is **named**, so a consumer writes
 * `@container board-header (max-width: …)` against it in its own styles, where
 * view encapsulation reaches the nodes it declared, and it pins `flex` on those
 * same nodes to steer where the cluster's shrink lands. `local-panel-layout.ts`
 * is the worked example — a truncating username, a menu that never gives way.
 */
@Component({
  selector: 'fleet-board-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BrandMark],
  template: `
    <header class="mc-header" data-testid="board-header">
      <div class="brand">
        <fleet-brand-mark [size]="30" />
        <div class="brand-text">blizzard<small>{{ tagline() }}</small></div>
      </div>
      <div class="stats" data-testid="board-header-stats">
        @for (cell of cells(); track cell.key) {
          <div class="cell" [attr.data-stat]="cell.key">
            <span class="stat-lbl">{{ cell.label }}</span>
            <span class="v" [attr.data-testid]="'stat-' + cell.key"
              >{{ cell.value }}{{ cell.capacity === undefined ? '' : '/' + cell.capacity }}</span
            >
          </div>
        }
      </div>
      <div class="spacer"></div>
      @if (spendToday(); as spend) {
        <!-- The fleet-wide spend-since read (issue #60) — "today" is whatever local
             start-of-day instant the caller passed as since; the cost-absent lower
             bound is marked, never presented as exact (a leading tilde, formatCost). -->
        <div class="cell spend" data-testid="spend-today">
          <span class="stat-lbl">Spend today</span>
          <span class="v" data-testid="spend-today-value">{{ formatCost(spend.cost_usd, spend.cost_partial) }}</span>
        </div>
      }
      <div class="cell conn" data-testid="conn">
        <span class="stat-lbl">{{ connectionLabel() }}</span>
        <span class="v">{{ connection() }}</span>
      </div>
      <!-- The trailing cluster is wrapped rather than projected straight into the
           flex row so this component can size it as one unit — view encapsulation
           puts the *consumer's* attribute on projected nodes, so a rule here
           could never reach them, and an unmanaged trailing cell is exactly how
           the profile menu used to get clipped (issue #163). -->
      <div class="trailing">
        <ng-content select="[header-trailing]" />
      </div>
    </header>
  `,
  styles: `
    :host {
      display: block;
      flex: none;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
    }
    .mc-header {
      display: flex;
      align-items: stretch;
      height: 48px;
      border-bottom: 1px solid var(--bezel);
      background: linear-gradient(180deg, var(--header-hi), var(--header-lo));
      /* The header is its own query container, not a viewport-media consumer:
         the hub board and the runner's local panel mount it at different
         widths, so each must collapse by the width *it* actually has (issue
         #163). Named so the container rules below can't be captured by some
         future nested container. */
      container-name: board-header;
      container-type: inline-size;
    }
    .stat-lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
      text-shadow: 0 1px 0 var(--overlay-90);
    }
    .brand {
      display: flex;
      flex: none;
      align-items: center;
      gap: 10px;
      padding: 0 14px;
      border-right: 1px solid var(--line);
      white-space: nowrap;
    }
    /* The one shrinkable region. Everything else is pinned flex: none, so an
       overfull header eats into the stat strip — which then clips — instead of
       pushing the connection cell and the profile menu past the clipped right
       edge of a viewport-locked, scrollbar-less shell. */
    .stats {
      display: flex;
      align-items: stretch;
      flex: 0 1 auto;
      min-width: 0;
      overflow: hidden;
    }
    .brand-text {
      display: flex;
      flex-direction: column;
      justify-content: center;
      color: var(--amber-hi);
      font-size: var(--fs-lg);
      letter-spacing: 0.28em;
      text-transform: uppercase;
    }
    .brand small {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
    }
    .cell {
      display: flex;
      flex: none;
      flex-direction: column;
      justify-content: center;
      padding: 0 14px;
      border-right: 1px solid var(--line);
      white-space: nowrap;
    }
    .cell .v {
      color: var(--amber);
      font-size: var(--fs-lg);
      line-height: 1.1;
    }
    /* The counts that carry an operator signal wear their status color, so a
       non-zero "needs human" reads as an alarm from across the room. */
    .cell[data-stat='needs'] .v {
      color: var(--red);
    }
    .cell[data-stat='waiting'] .v {
      color: var(--amber-hi);
    }
    .cell[data-stat='done'] .v {
      color: var(--green);
    }
    .spacer {
      flex: 1;
      border-right: 1px solid var(--line);
    }
    .conn {
      border-right: none;
    }
    .conn .v {
      color: var(--cyan);
    }
    .spend .v {
      color: var(--amber-hi);
    }
    /*
     * The trailing cluster shrinks rather than overflowing, but only into
     * whatever its consumer marked shrinkable — a consumer projecting content of
     * *content-dependent* width (the runner's local panel projects a signed-in
     * username) has to be able to truncate it, and a flex: none wrapper is sized
     * to its max-content, so a long username would push the whole cluster, menu
     * included, past the clipped right edge. A zero min-width removes that floor;
     * each projected control then decides for itself whether it gives way (see
     * local-panel-layout.ts, where the identity truncates and the menu is pinned
     * flex: none).
     */
    .trailing {
      display: flex;
      flex: 0 1 auto;
      min-width: 0;
      align-items: stretch;
    }

    /*
     * Tiered collapse (issue #163). Below the wide breakpoint the per-lane
     * count cells go — the board's own lane columns still carry those headings,
     * so nothing is lost — and below the narrow one the spend cell and the whole
     * brand *text* block follow, leaving the brand mark, the connection cell,
     * and the projected menu. Those three survive every tier: on a phone forced
     * into desktop mode that menu is the only way back to mobile, so it must
     * never be the thing that gets pushed off.
     *
     * The wordmark goes with the tagline rather than after it. Together they are
     * ~190px of a 390px phone, which is the difference between the trailing
     * cluster fitting and running off the edge on the runner's local panel,
     * whose trailing slot carries more than the hub's — and the mark alone still
     * identifies the app.
     */
    @container board-header (max-width: 1149px) {
      .stats {
        display: none;
      }
    }
    @container board-header (max-width: 699px) {
      .spend {
        display: none;
      }
      .brand-text {
        display: none;
      }
    }
  `,
})
export class BoardHeader {
  protected readonly formatCost = formatCost;

  /** A short connection/health status shown in the header (e.g. `ok`, `offline`). */
  readonly connection = input('—');

  /** The connection cell's label — `Hub` for the hub board, `Runner` for the
   * runner's local panel. */
  readonly connectionLabel = input('Hub');

  /** The brand block's subtitle line. */
  readonly tagline = input('fleet hub · mission control');

  /** The fleet chunk list the counts are derived from — the hub's usage; ignored
   * once {@link stats} is given. */
  readonly chunks = input<readonly ChunkSummary[]>([]);

  /** The fleet-wide spend-since read (issue #60), or `null` before the first read
   * resolves — the cell withholds itself rather than show a misleading `$0.00`. */
  readonly spendToday = input<FleetSpendView | null>(null);

  /** Explicit stat cells, e.g. the runner's envs/agents capacity cells (issue
   * #131) — when given, these render in place of {@link chunkStats} below, so a
   * caller with no chunk list supplies its own stats without this component
   * knowing anything about its domain. `null` (the hub's usage) falls through to
   * the chunk-derived lane cells. */
  readonly stats = input<readonly StatCell[] | null>(null);

  /**
   * The live fleet counts, left → right: the whole fleet, then one cell per board
   * lane in the board's own order — Ready among them, no longer a special case.
   *
   * Every count is grouped through {@link laneFor} rather than by naming statuses
   * here. The header sits directly above the board and must not be able to disagree
   * with it: a status this header listed and the board did not (or the reverse) would
   * be a silent contradiction, whereas a new status added to the wire is a compile
   * error in `chunk-lanes`, the one place that decides where it belongs. The Ready
   * cell used to count the lane-less `ready` status the left rail owned; the READY
   * lane (issue #137) makes it a plain lane tally like every other cell.
   */
  protected readonly chunkStats = computed<readonly StatCell[]>(() => {
    const chunks = this.chunks();
    const perLane = new Map<string, number>(LANES.map((lane) => [lane.key, 0]));
    for (const chunk of chunks) {
      const lane = laneFor(chunk.status);
      perLane.set(lane, (perLane.get(lane) ?? 0) + 1);
    }
    return [
      { key: 'total', label: 'Chunks', value: chunks.length },
      ...LANES.map((lane) => ({
        key: lane.key,
        label: lane.headerLabel,
        value: perLane.get(lane.key) ?? 0,
      })),
    ];
  });

  /** The cells the template renders — {@link stats} when the caller supplied one,
   * else {@link chunkStats}. */
  protected readonly cells = computed<readonly StatCell[]>(() => this.stats() ?? this.chunkStats());
}

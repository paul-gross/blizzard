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

/** One spend cell — today's or yesterday's (issue #183) — folded into one shape
 * so the template renders both from a single `@for` rather than a duplicated
 * `@if` block per window. `spend` is `null` while its own read is unresolved;
 * the template withholds the cell entirely rather than show a misleading `$0.00`. */
interface SpendCellView {
  readonly key: 'today' | 'yesterday';
  readonly label: string;
  readonly testid: string;
  readonly valueTestid: string;
  readonly spend: FleetSpendView | null;
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
  templateUrl: './board-header.html',
  styleUrl: './board-header.css',
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

  /** The fleet-wide spend-yesterday read (issue #183) — `[yesterday-midnight,
   * today-midnight)`, or `null` before the first read resolves (withheld the same
   * way as {@link spendToday}) and for every consumer that never passes one, e.g.
   * the runner's local panel (`local-panel-layout.ts`), which has no such read. */
  readonly spendYesterday = input<FleetSpendView | null>(null);

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

  /** Today's and yesterday's spend cells (issue #183), folded into one list the
   * template renders with a single `@for` — see {@link SpendCellView}. */
  protected readonly spendCells = computed<readonly SpendCellView[]>(() => [
    { key: 'today', label: 'TODAY', testid: 'spend-today', valueTestid: 'spend-today-value', spend: this.spendToday() },
    {
      key: 'yesterday',
      label: 'YESTERDAY',
      testid: 'spend-yesterday',
      valueTestid: 'spend-yesterday-value',
      spend: this.spendYesterday(),
    },
  ]);
}

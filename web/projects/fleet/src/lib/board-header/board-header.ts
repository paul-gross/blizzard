import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { ChunkSummary } from '../api/hub';
import { LANES, laneFor } from '../chunk-lanes';

/** One header stat cell — a label over its live count. */
interface StatCell {
  readonly key: string;
  readonly label: string;
  readonly value: number;
}

/**
 * The mission-control titlebar — the brand, the fleet's live counts, and
 * the hub connection state. It spans the whole window above the three columns, so
 * it lives here rather than inside {@link BoardShell}: a header nested in the board
 * column would only span that column, leaving the rails to start above it.
 *
 * Presentational only: it derives its counts from the chunk list it is handed.
 * All color comes from the design-token layer, never hard-coded hex.
 */
@Component({
  selector: 'fleet-board-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="mc-header" data-testid="board-header">
      <div class="brand">
        blizzard<small>fleet hub · mission control</small>
      </div>
      @for (cell of stats(); track cell.key) {
        <div class="cell" [attr.data-stat]="cell.key">
          <span class="lbl">{{ cell.label }}</span>
          <span class="v" [attr.data-testid]="'stat-' + cell.key">{{ cell.value }}</span>
        </div>
      }
      <div class="spacer"></div>
      <div class="cell conn" data-testid="conn">
        <span class="lbl">Hub</span>
        <span class="v">{{ connection() }}</span>
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
    }
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
      text-shadow: 0 1px 0 rgba(0, 0, 0, 0.9);
    }
    .brand {
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 0 14px;
      border-right: 1px solid var(--line);
      color: var(--amber-hi);
      font-size: var(--fs-lg);
      letter-spacing: 0.28em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .brand small {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
    }
    .cell {
      display: flex;
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
  `,
})
export class BoardHeader {
  /** A short connection/health status shown in the header (e.g. `ok`, `offline`). */
  readonly connection = input('—');

  /** The fleet chunk list the counts are derived from. */
  readonly chunks = input<readonly ChunkSummary[]>([]);

  /**
   * The live fleet counts, left → right: the whole fleet, the ready rail, then one
   * cell per board lane in the board's own order.
   *
   * Every count is grouped through {@link laneFor} rather than by naming statuses
   * here. The header sits directly above the board and must not be able to disagree
   * with it: a status this header listed and the board did not (or the reverse) would
   * be a silent contradiction, whereas a new status added to the wire is a compile
   * error in `chunk-lanes`, the one place that decides where it belongs.
   */
  protected readonly stats = computed<readonly StatCell[]>(() => {
    const chunks = this.chunks();
    const perLane = new Map<string, number>(LANES.map((lane) => [lane.key, 0]));
    // A null lane means the chunk is in the ready rail rather than on the board —
    // that is what the Ready cell counts, derived rather than re-named.
    let ready = 0;
    for (const chunk of chunks) {
      const lane = laneFor(chunk.status);
      if (lane === null) ready++;
      else perLane.set(lane, (perLane.get(lane) ?? 0) + 1);
    }
    return [
      { key: 'total', label: 'Chunks', value: chunks.length },
      { key: 'ready', label: 'Ready', value: ready },
      ...LANES.map((lane) => ({
        key: lane.key,
        label: lane.headerLabel,
        value: perLane.get(lane.key) ?? 0,
      })),
    ];
  });
}

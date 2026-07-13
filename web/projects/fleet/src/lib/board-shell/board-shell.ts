import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** The five board columns, in dispatch → done order (workflow build → review → deliver). */
interface BoardColumn {
  readonly key: string;
  readonly label: string;
}

const COLUMNS: readonly BoardColumn[] = [
  { key: 'ready', label: 'READY' },
  { key: 'running', label: 'RUNNING' },
  { key: 'waiting', label: 'WAIT/HUMAN' },
  { key: 'needs', label: 'NEEDS HUMAN' },
  { key: 'done', label: 'DONE' },
];

/**
 * The mission-control board shell — header, the empty five-column board grid,
 * and an empty-state message (D-097). This is the shared fleet view the hub app
 * renders; it lives once here so the runner app can compose it too. Presentational
 * only: it holds no data and no data client — chunks, counts, and live wiring land
 * on top of this shell as the board features arrive. All color comes from the
 * design-token layer (design/tokens.css), never hard-coded hex.
 */
@Component({
  selector: 'fleet-board-shell',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="mc" data-testid="board-shell">
      <header class="mc-header">
        <div class="brand">
          blizzard<small>fleet hub · mission control</small>
        </div>
        <div class="spacer"></div>
        <div class="conn" data-testid="conn">
          <span class="lbl">Hub</span>
          <span class="v">{{ connection() }}</span>
        </div>
      </header>

      <section class="panel board-panel" aria-label="Chunk board">
        <div class="panel-head">
          <span class="lbl">Chunk board · workflow build → review → deliver</span>
          <span class="lbl">graph: default</span>
        </div>
        <div class="board" data-testid="board">
          @for (col of columns; track col.key) {
            <div class="b-col" [attr.data-col]="col.key">
              <div class="b-col-head">
                <span class="lbl">{{ col.label }}</span>
                <span class="n">0</span>
              </div>
              <div class="b-col-body"></div>
            </div>
          }
        </div>
        <p class="empty" data-testid="empty-state">NO CHUNKS — FLEET IDLE</p>
      </section>
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      background: var(--bg);
      color: var(--text);
      font-family: var(--mono);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }
    .mc {
      display: flex;
      flex-direction: column;
      height: 100%;
    }
    .lbl {
      font-size: 9px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
      text-shadow: 0 1px 0 rgba(0, 0, 0, 0.9);
    }
    .mc-header {
      flex: none;
      display: flex;
      align-items: stretch;
      height: 40px;
      border-bottom: 1px solid var(--bezel);
      background: linear-gradient(180deg, #0d1526, #080d18);
    }
    .brand {
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 0 14px;
      border-right: 1px solid var(--line);
      color: var(--amber-hi);
      font-size: 15px;
      letter-spacing: 0.28em;
      text-transform: uppercase;
    }
    .brand small {
      color: var(--label);
      font-size: 9px;
      letter-spacing: 0.18em;
    }
    .spacer {
      flex: 1;
      border-right: 1px solid var(--line);
    }
    .conn {
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 0 14px;
    }
    .conn .v {
      color: var(--cyan);
      font-size: 15px;
      line-height: 1.1;
    }
    .panel {
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
      border: 1px solid var(--bezel);
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .board-panel {
      flex: 1;
      margin: 6px;
      position: relative;
    }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.25);
      flex: none;
    }
    .board {
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 1px;
      background: var(--line);
      flex: 1;
      min-height: 0;
    }
    .b-col {
      background: var(--panel-deep);
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .b-col-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      padding: 4px 6px;
      border-bottom: 1px solid var(--line);
      flex: none;
    }
    .b-col-head .n {
      font-size: 13px;
      color: var(--label-dim);
    }
    .b-col-body {
      overflow-y: auto;
      flex: 1;
      padding: 4px;
    }
    .empty {
      position: absolute;
      left: 50%;
      top: 55%;
      transform: translate(-50%, -50%);
      color: var(--label-dim);
      font-size: 11px;
      letter-spacing: 0.12em;
      pointer-events: none;
    }
  `,
})
export class BoardShell {
  /** A short connection/health status shown in the header (e.g. `ok`, `offline`). */
  readonly connection = input('—');

  protected readonly columns = COLUMNS;
}

import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ChunkStatus, ChunkSummary } from '../api/hub';

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
 * Map a chunk's derived status (D-004) onto its board column. The walking-skeleton
 * board has one column per resting state; the transient `delivering` shows under
 * RUNNING, and terminal `stopped` shows under DONE.
 */
const STATUS_COLUMN: Record<ChunkStatus, string> = {
  ready: 'ready',
  running: 'running',
  delivering: 'running',
  waiting_on_human: 'waiting',
  needs_human: 'needs',
  stopped: 'done',
  done: 'done',
};

/** One linked PM-pointer chip — the server-derived `{code}:{repo}#{number}` label (D-075). */
export interface PointerChip {
  readonly label: string;
  readonly url: string;
}

/** One rendered board card — the derived-status view of a chunk. */
export interface BoardCard {
  readonly chunkId: string;
  readonly shortId: string;
  readonly status: ChunkStatus;
  /** The node's human graph name (`build`, `review`); falls back to the raw id. */
  readonly node: string;
  /** The raw `nd_` ULID, kept reachable as the node label's tooltip. */
  readonly nodeId: string;
  readonly pointers: readonly PointerChip[];
}

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
                <span class="n">{{ cardsFor(col.key).length }}</span>
              </div>
              <div class="b-col-body">
                @for (card of cardsFor(col.key); track card.chunkId) {
                  <div class="card" data-testid="chunk-card" [attr.data-status]="card.status">
                    @if (card.pointers.length > 0) {
                      <div class="chips">
                        @for (p of card.pointers; track p.url) {
                          <a
                            class="chip"
                            data-testid="pm-chip"
                            [href]="p.url"
                            target="_blank"
                            rel="noreferrer"
                            [attr.title]="p.url"
                            >{{ p.label }}</a
                          >
                        }
                      </div>
                    }
                    <button
                      type="button"
                      class="card-open"
                      [attr.aria-label]="'Open chunk ' + card.shortId"
                      (click)="selectChunk.emit(card.chunkId)"
                    >
                      <div class="card-id" data-testid="chunk-id">{{ card.shortId }}</div>
                      <div class="card-meta">
                        <span class="st" data-testid="chunk-status">{{ card.status }}</span>
                        <span class="nd" data-testid="chunk-node" [attr.title]="card.nodeId || null">{{
                          card.node
                        }}</span>
                      </div>
                    </button>
                  </div>
                }
              </div>
            </div>
          }
        </div>
        @if (total() === 0) {
          <p class="empty" data-testid="empty-state">NO CHUNKS — FLEET IDLE</p>
        }
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
    /* The DONE column carries the mockup's green treatment (flow-board.html): a
       green header label + head accent, and green card accents, all from tokens. */
    .b-col[data-col='done'] .b-col-head {
      border-bottom-color: var(--green-dim);
    }
    .b-col[data-col='done'] .b-col-head .lbl {
      color: var(--green);
    }
    .b-col[data-col='done'] .card {
      border-left-color: var(--green);
    }
    .b-col-body {
      overflow-y: auto;
      overflow-x: hidden;
      flex: 1;
      padding: 4px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .card {
      box-sizing: border-box;
      border: 1px solid var(--line);
      border-left: 3px solid var(--amber);
      background: rgba(0, 0, 0, 0.25);
      padding: 4px 6px;
      display: flex;
      flex-direction: column;
      gap: 3px;
      width: 100%;
      overflow-wrap: anywhere;
    }
    .card:hover {
      border-color: var(--cyan);
    }
    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 3px;
    }
    .chip {
      border: 1px solid var(--line);
      padding: 0 4px;
      color: var(--amber-hi);
      font-size: 9px;
      letter-spacing: 0.08em;
      text-decoration: none;
    }
    .chip:hover,
    .chip:focus-visible {
      border-color: var(--amber);
      outline: none;
    }
    .card-open {
      border: 0;
      background: transparent;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 3px;
      width: 100%;
      text-align: left;
      font: inherit;
      color: inherit;
      cursor: pointer;
    }
    .card-open:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: 1px;
    }
    .card-id {
      color: var(--cyan);
      font-size: 11px;
      letter-spacing: 0.04em;
    }
    .card-meta {
      display: flex;
      justify-content: space-between;
      gap: 6px;
    }
    .card-meta .st {
      color: var(--amber-hi);
      font-size: 9px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
    .card-meta .nd {
      color: var(--label-dim);
      font-size: 9px;
      letter-spacing: 0.1em;
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

  /** The fleet chunk list (derived status + current node); empty when the fleet is idle. */
  readonly chunks = input<readonly ChunkSummary[]>([]);

  /** Emitted with a chunk id when its card is activated — opens the detail drawer. */
  readonly selectChunk = output<string>();

  protected readonly columns = COLUMNS;

  /** Every chunk rendered as a board card, grouped into its status column. */
  private readonly cards = computed<Map<string, BoardCard[]>>(() => {
    const grouped = new Map<string, BoardCard[]>(COLUMNS.map((c) => [c.key, []]));
    for (const chunk of this.chunks()) {
      const column = STATUS_COLUMN[chunk.status] ?? 'ready';
      grouped.get(column)?.push({
        chunkId: chunk.chunk_id,
        shortId: chunk.chunk_id.slice(0, 12),
        status: chunk.status,
        node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
        nodeId: chunk.current_node_id ?? '',
        // Only labeled pointers render as chips — an unparseable pointer URL has a
        // null label and the card leans on the short id instead (issue-shape degrade).
        pointers: (chunk.pm_pointers ?? []).flatMap((p) => (p.label ? [{ label: p.label, url: p.url }] : [])),
      });
    }
    return grouped;
  });

  protected readonly total = computed(() => this.chunks().length);

  protected cardsFor(columnKey: string): readonly BoardCard[] {
    return this.cards().get(columnKey) ?? [];
  }
}

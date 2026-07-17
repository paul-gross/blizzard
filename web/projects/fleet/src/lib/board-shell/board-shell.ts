import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ChunkStatus, ChunkSummary } from '../api/hub';
import { shortChunkId } from '../chunk-id';
import { LANES, STATUS_LANE } from '../chunk-lanes';

/** One rendered board card — the derived-status view of a chunk. */
export interface BoardCard {
  readonly chunkId: string;
  readonly shortId: string;
  readonly status: ChunkStatus;
  /** The node's human graph name (`build`, `review`); falls back to the raw id. */
  readonly node: string;
  /** The raw `nd_` ULID, kept reachable as the node label's tooltip. */
  readonly nodeId: string;
  /** The chunk's PM work item — the server-derived `{source}#{ref}` label,
   * empty when no pointer names a configured source. Plural pointers join with a space. */
  readonly pointerLabel: string;
}

/**
 * The mission-control chunk board — the five status columns and their
 * cards, filling the centre column above the chunk detail. The titlebar is not
 * here: it spans all three columns, so {@link BoardHeader} owns it.
 *
 * This is the shared fleet view the hub app renders; it lives once here so the
 * runner app can compose it too. Presentational only: it holds no data client.
 * All color comes from the design-token layer (design/tokens.css), never
 * hard-coded hex, and every text size from that layer's type scale.
 */
@Component({
  selector: 'fleet-board-shell',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="mc" data-testid="board-shell">
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
                    <button
                      type="button"
                      class="card-open"
                      [attr.aria-label]="'Open chunk ' + card.shortId"
                      (click)="selectChunk.emit(card.chunkId)"
                    >
                      <span class="tid">
                        <span class="card-id" data-testid="chunk-id">{{ card.shortId }}</span>
                        <span class="nd" data-testid="chunk-node" [attr.title]="card.nodeId || null">{{
                          card.node
                        }}</span>
                      </span>
                      <!-- The pointer label is plain text here, not a link: a card is a
                           target for opening the chunk, and an anchor inside it competes
                           for the same click. The detail panel owns the link out to the PM. -->
                      @if (card.pointerLabel) {
                        <span class="iss" data-testid="pm-chip" [title]="card.pointerLabel">{{
                          card.pointerLabel
                        }}</span>
                      }
                      <span class="st" data-testid="chunk-status" [title]="card.status">{{ card.status }}</span>
                    </button>
                    @if (card.status === 'not_ready') {
                      <button
                        type="button"
                        class="card-promote"
                        data-testid="promote-chunk"
                        [attr.aria-label]="'Promote chunk ' + card.shortId + ' to ready'"
                        (click)="promote.emit(card.chunkId)"
                      >
                        PROMOTE
                      </button>
                    }
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
      min-height: 0;
      color: var(--text);
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
    }
    .mc {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
    }
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
      text-shadow: 0 1px 0 rgba(0, 0, 0, 0.9);
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
      font-size: var(--fs-md);
      color: var(--label-dim);
    }
    /* The DONE column carries a green treatment: a green header label + head
       accent, and green card accents, all from tokens. */
    .b-col[data-col='done'] .b-col-head {
      border-bottom-color: var(--green-dim);
    }
    .b-col[data-col='done'] .b-col-head .lbl {
      color: var(--green);
    }
    .b-col[data-col='done'] .card {
      border-left-color: var(--green);
    }
    /* The NOT READY backlog column reads as held/inert: a muted header label and a
       dim card accent, distinct from the ready queue in the rail and from any live
       lane. Colors come from tokens, never hard-coded hex. */
    .b-col[data-col='notready'] .b-col-head .lbl {
      color: var(--label-dim);
    }
    .card[data-status='not_ready'] {
      border-left-color: var(--label-dim);
    }
    .card-promote {
      align-self: flex-start;
      border: 1px solid var(--amber-dim);
      background: transparent;
      color: var(--amber-hi);
      padding: 1px 6px;
      font: inherit;
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      cursor: pointer;
    }
    .card-promote:hover,
    .card-promote:focus-visible {
      border-color: var(--amber);
      outline: none;
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
      min-width: 0;
    }
    .card:hover {
      border-color: var(--cyan);
    }
    .card-open {
      border: 0;
      background: transparent;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 2px;
      width: 100%;
      min-width: 0;
      text-align: left;
      font: inherit;
      color: inherit;
      cursor: pointer;
    }
    .card-open:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: 1px;
    }
    /* The card's identity line: the chunk's short name, with the node it currently
       sits at pushed to the far right.

       Every line here holds to one line and ellipsises instead of wrapping. A board
       column is narrow, and a wrapped card is worse than a clipped one twice over: it
       breaks a value mid-token (a chunk name split across two lines is unreadable and
       unsearchable) and it makes cards in the same column different heights, so the
       column stops scanning as a list. The full value stays reachable in the detail. */
    .tid {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 6px;
      min-width: 0;
    }
    .card-id {
      color: var(--amber);
      font-size: var(--fs-md);
      letter-spacing: 0.04em;
      white-space: nowrap;
    }
    .tid .nd {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.12em;
      text-transform: uppercase;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .iss,
    .st {
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .iss {
      color: var(--cyan);
      font-size: var(--fs-xs);
    }
    .st {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
    .empty {
      position: absolute;
      left: 50%;
      top: 55%;
      transform: translate(-50%, -50%);
      color: var(--label-dim);
      font-size: var(--fs-sm);
      letter-spacing: 0.12em;
      pointer-events: none;
    }
  `,
})
export class BoardShell {
  /** The fleet chunk list (derived status + current node); empty when the fleet is idle. */
  readonly chunks = input<readonly ChunkSummary[]>([]);

  /** Emitted with a chunk id when its card is activated — fills the detail dock. */
  readonly selectChunk = output<string>();

  /** Emitted with a chunk id when a not-ready card's Promote is clicked. */
  readonly promote = output<string>();

  protected readonly columns = LANES;

  /** Every chunk rendered as a board card, grouped into its status column. */
  private readonly cards = computed<Map<string, BoardCard[]>>(() => {
    const grouped = new Map<string, BoardCard[]>(LANES.map((lane) => [lane.key, []]));
    for (const chunk of this.chunks()) {
      const column = STATUS_LANE[chunk.status];
      // Ready chunks belong to the left rail (fleet-queue-panel), not the board —
      // a null column skips them so they never double-show as a board card (issue #22).
      if (!column) continue;
      grouped.get(column)?.push({
        chunkId: chunk.chunk_id,
        shortId: shortChunkId(chunk.chunk_id),
        status: chunk.status,
        node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
        nodeId: chunk.current_node_id ?? '',
        // Only labeled pointers show — a pointer naming no configured source has a
        // null label and the card leans on the short id instead.
        pointerLabel: (chunk.pm_pointers ?? [])
          .flatMap((p) => (p.label ? [p.label] : []))
          .join(' '),
      });
    }
    return grouped;
  });

  protected readonly total = computed(() => this.chunks().length);

  protected cardsFor(columnKey: string): readonly BoardCard[] {
    return this.cards().get(columnKey) ?? [];
  }
}

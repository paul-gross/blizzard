import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ChunkSummary } from '../api/hub';
import type { BoardCard } from '../board-card/board-card';
import { BoardColumn, type BoardReposition } from './board-column';
import { compactRef } from '../compact-ref';
import { LANES, STATUS_LANE } from '../chunk-lanes';
import { KitPanel } from '../kit/kit-panel';

export type { BoardCard, BoardReposition };

/**
 * The mission-control chunk board — the six status columns and their
 * cards, filling the centre column above the chunk detail. The titlebar is not
 * here: it spans the whole window, so {@link BoardHeader} owns it.
 *
 * READY is one of those columns (issue #137), not a rail beside them: the ready
 * queue is dispatch order, so it renders top-to-bottom in the order the hub will
 * hand work out ({@link readyOrder}) and is reshaped in place — drag a card, or
 * use its Top button, and {@link reposition}/{@link moveToTop} leave for whoever
 * owns the write. {@link group} carries the lane's multi-select. Every one of
 * those affordances belongs to READY alone; {@link BoardColumn} arms them off a
 * single flag.
 *
 * This is the shared fleet view the hub app renders; it lives once here so the
 * runner app can compose it too. Presentational only: it holds no data client.
 * All color comes from the design-token layer (design/tokens.css), never
 * hard-coded hex, and every text size from that layer's type scale.
 */
@Component({
  selector: 'fleet-board-shell',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardColumn, KitPanel],
  template: `
    <div class="mc" data-testid="board-shell">
      <fleet-kit-panel
        class="board-panel"
        aria-label="Chunk board"
        label="Chunk board · workflow build → review → deliver"
      >
        <span header class="col-lbl">graph: default</span>
        <div class="board" data-testid="board">
          @for (col of columns; track col.key) {
            <fleet-board-column
              [column]="col"
              [cards]="cardsFor(col.key)"
              [selectedChunkId]="selectedChunkId()"
              [queueControls]="col.key === 'ready'"
              (selectChunk)="selectChunk.emit($event)"
              (promote)="promote.emit($event)"
              (reposition)="reposition.emit($event)"
              (moveToTop)="moveToTop.emit($event)"
              (group)="group.emit($event)"
            />
          }
        </div>
        @if (total() === 0) {
          <p class="empty" data-testid="empty-state">NO CHUNKS — FLEET IDLE</p>
        }
      </fleet-kit-panel>
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
    /* The board's own engraved label: the second panel-head span, projected into
       the kit panel's header slot. Content projected into a child component keeps
       *this* component's style scope (Angular content projection doesn't move a
       node into the child's encapsulation), so this is a local rule, not a
       re-typed copy of the kit panel's own label chrome — same look, kept
       separate from the retired chrome class names so the structural gate stays
       honest. Each column's own head label is BoardColumn's, for the same reason
       in reverse: a rule here could not reach into its encapsulation. */
    .col-lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
      text-shadow: 0 1px 0 var(--overlay-90);
    }
    fleet-kit-panel.board-panel {
      flex: 1;
      position: relative;
    }
    /* One equal track per lane, laid out by flow rather than a repeat() count —
       LANES is the single owner of how many lanes there are (issue #137 added
       READY), and a hard-coded count here is a second place to forget. */
    .board {
      display: grid;
      grid-auto-flow: column;
      grid-auto-columns: 1fr;
      gap: 1px;
      background: var(--line);
      flex: 1;
      min-height: 0;
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

  /**
   * The ready chunk ids in hub dispatch order — the top of the list is what the
   * next acquire takes. The READY lane renders in exactly this order; a ready
   * chunk the order does not name (a promote the queue read has not caught up
   * with yet) sorts after the ones it does, keeping its relative order, rather
   * than jumping the queue or vanishing.
   */
  readonly readyOrder = input<readonly string[]>([]);

  /** Emitted with a chunk id when its card is activated — fills the detail dock. */
  readonly selectChunk = output<string>();

  /** The chunk whose detail currently fills the dock, or null — its card carries
   * the selection highlight so the board says which one is open. */
  readonly selectedChunkId = input<string | null>(null);

  /** Emitted with a chunk id when a backlog card's Promote is clicked. */
  readonly promote = output<string>();

  /** Emitted when a READY card is dragged somewhere new — the chunk and the
   * anchor it now sits after (`null` = the very top of the queue). */
  readonly reposition = output<BoardReposition>();

  /** Emitted with a chunk id when a READY card's Top button is clicked. */
  readonly moveToTop = output<string>();

  /** Emitted with the READY lane's multi-selection, in lane order (the top-most
   * is the group survivor), when the operator activates Group. */
  readonly group = output<readonly string[]>();

  protected readonly columns = LANES;

  /** Every chunk rendered as a board card, grouped into its status column. */
  private readonly cards = computed<Map<string, BoardCard[]>>(() => {
    const grouped = new Map<string, BoardCard[]>(LANES.map((lane) => [lane.key, []]));
    for (const chunk of this.chunks()) {
      grouped.get(STATUS_LANE[chunk.status])?.push({
        chunkId: chunk.chunk_id,
        shortId: compactRef(chunk.chunk_id),
        status: chunk.status,
        node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
        nodeId: chunk.current_node_id ?? '',
        // Only labeled pointers show — a pointer naming no configured source has a
        // null label and the card leans on the short id instead.
        pointerLabel: (chunk.work_refs ?? [])
          .flatMap((p) => (p.label ? [p.label] : []))
          .join(' '),
        costUsd: chunk.cost?.cost_usd ?? 0,
        costPartial: chunk.cost?.cost_partial ?? false,
        completedAt: chunk.completed_at ?? null,
      });
    }
    // READY alone is ordered rather than listed: it is a queue, so its rank comes
    // from the hub's own dispatch order. Unranked ids sort to the end on a stable
    // sort, which keeps their relative order among themselves.
    const rank = new Map(this.readyOrder().map((chunkId, index) => [chunkId, index]));
    const rankOf = (card: BoardCard): number => rank.get(card.chunkId) ?? Number.POSITIVE_INFINITY;
    grouped.get('ready')?.sort((a, b) => {
      // Subtracting the ranks would be NaN for two unranked cards, and a NaN
      // comparator forfeits the stability this leans on.
      const [left, right] = [rankOf(a), rankOf(b)];
      if (left === right) return 0;
      return left < right ? -1 : 1;
    });
    // DONE alone is ordered newest-first (issue #173) — a second lane-scoped
    // ordering, same shape as READY's above. A `null` completedAt (shouldn't happen
    // for a done-lane card, but not fabricated if it does) sorts last rather than
    // jumping to the top, and ties keep their relative order (stable sort).
    const completedRank = (card: BoardCard): number => {
      const ms = card.completedAt === null ? null : Date.parse(card.completedAt);
      return ms === null || Number.isNaN(ms) ? Number.NEGATIVE_INFINITY : ms;
    };
    grouped.get('done')?.sort((a, b) => {
      const [left, right] = [completedRank(a), completedRank(b)];
      if (left === right) return 0;
      return left > right ? -1 : 1;
    });
    return grouped;
  });

  protected readonly total = computed(() => this.chunks().length);

  protected cardsFor(columnKey: string): readonly BoardCard[] {
    return this.cards().get(columnKey) ?? [];
  }
}

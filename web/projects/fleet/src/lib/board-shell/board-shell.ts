import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ChunkStatus, ChunkSummary } from '../api/hub';
import { compactRef } from '../compact-ref';
import { LANES, STATUS_LANE } from '../chunk-lanes';
import { formatCost } from '../cost-format';
import { KitPanel } from '../kit/kit-panel';

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
  /** The chunk's derived spend total (issue #60), from `ChunkSummary.cost`. */
  readonly costUsd: number;
  /** Whether {@link costUsd} is a lower bound — a summed invocation's envelope-less
   * cost was absent (crash/reap path); never presented as exact. */
  readonly costPartial: boolean;
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
  imports: [KitPanel],
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
            <div class="b-col" [attr.data-col]="col.key">
              <div class="b-col-head">
                <span class="col-lbl">{{ col.label }}</span>
                <span class="n">
                  <!-- A live lane with occupants announces itself: a flashing square
                       ahead of the count — amber for work in flight or parked on a
                       human, red for an escalation. Quiet (empty) lanes show none. -->
                  @if (cardsFor(col.key).length > 0 && blinkFor(col.key); as blink) {
                    <span class="blink" data-testid="lane-blink" [attr.data-blink]="blink"></span>
                  }
                  {{ cardsFor(col.key).length }}
                </span>
              </div>
              <div class="b-col-body">
                @for (card of cardsFor(col.key); track card.chunkId) {
                  <div
                    class="card"
                    data-testid="chunk-card"
                    [attr.data-status]="card.status"
                    [class.selected]="card.chunkId === selectedChunkId()"
                    [attr.aria-current]="card.chunkId === selectedChunkId() ? 'true' : null"
                  >
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
                      <span class="st-row">
                        <span class="st" data-testid="chunk-status" [title]="card.status">{{ card.status }}</span>
                        @if (card.costUsd > 0 || card.costPartial) {
                          <span class="cost" data-testid="card-cost">{{
                            formatCost(card.costUsd, card.costPartial)
                          }}</span>
                        }
                      </span>
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
    /* The board's own engraved labels: the second panel-head span (projected into
       the kit panel's header slot) and every column's header label. Content
       projected into a child component keeps *this* component's style scope
       (Angular content projection doesn't move a node into the child's
       encapsulation), so this is a local rule, not a re-typed copy of the kit
       panel's own label chrome — same look, kept separate from the retired
       chrome class names so the structural gate stays honest. */
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
      display: flex;
      align-items: center;
      gap: 5px;
      font-size: var(--fs-md);
      color: var(--label-dim);
    }
    /* Per the mockup, the header labels stay uniformly grey and the **count** carries
       each lane's color: amber for live work (running, parked on a human), red for an
       escalation, green for done — all from tokens. */
    .b-col[data-col='running'] .b-col-head .n,
    .b-col[data-col='waiting'] .b-col-head .n {
      color: var(--amber);
    }
    .b-col[data-col='needs'] .b-col-head .n {
      color: var(--red);
    }
    .b-col[data-col='done'] .b-col-head .n {
      color: var(--green);
    }
    /* The occupied-lane beacon ahead of the count — see the template note. */
    .blink {
      width: 7px;
      height: 7px;
      background: var(--amber);
      animation: lane-blink 2s ease-in-out infinite;
    }
    .blink[data-blink='red'] {
      background: var(--red);
    }
    @keyframes lane-blink {
      50% {
        opacity: 0;
      }
    }
    /* The DONE column keeps its green head accent and card accents. */
    .b-col[data-col='done'] .b-col-head {
      border-bottom-color: var(--green-dim);
    }
    .b-col[data-col='done'] .card {
      border-left-color: var(--green);
    }
    /* An escalated chunk reads in the alarm color: the card's left bar and its name,
       matching the mockup's NEEDS HUMAN treatment. */
    .b-col[data-col='needs'] .card {
      border-left-color: var(--red);
    }
    .b-col[data-col='needs'] .card-id {
      color: var(--red);
    }
    /* The NOT READY backlog column reads as held/inert: a muted header label and a
       dim card accent, distinct from the ready queue in the rail and from any live
       lane. Colors come from tokens, never hard-coded hex. */
    .b-col[data-col='notready'] .b-col-head .col-lbl {
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
      background: var(--overlay-25);
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
    /* The chunk whose detail fills the dock — an outline ring (not border-color, which
       would repaint the status-colored left bar) plus a faint cyan wash, so the
       board answers "which one am I looking at" at a glance. */
    .card.selected {
      outline: 1px solid var(--cyan);
      outline-offset: -1px;
      background: color-mix(in srgb, var(--cyan) 8%, var(--overlay-25));
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
    .st-row {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 6px;
      min-width: 0;
    }
    .st {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
    /* The chunk's derived spend total (issue #60) — the leading-tilde lower-bound
       prefix (formatCost) is the card's whole PARTIAL marker; no separate badge on a
       card this small. */
    .cost {
      color: var(--amber-hi);
      font-size: var(--fs-xs);
      white-space: nowrap;
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
  protected readonly formatCost = formatCost;

  /** The fleet chunk list (derived status + current node); empty when the fleet is idle. */
  readonly chunks = input<readonly ChunkSummary[]>([]);

  /** Emitted with a chunk id when its card is activated — fills the detail dock. */
  readonly selectChunk = output<string>();

  /** The chunk whose detail currently fills the dock, or null — its card carries
   * the selection highlight so the board says which one is open. */
  readonly selectedChunkId = input<string | null>(null);

  /** Emitted with a chunk id when a not-ready card's Promote is clicked. */
  readonly promote = output<string>();

  protected readonly columns = LANES;

  /** The beacon color for an occupied lane's header, or null for the quiet lanes:
   * amber for work in flight or parked on a human, red for an escalation. */
  protected blinkFor(columnKey: string): 'amber' | 'red' | null {
    if (columnKey === 'running' || columnKey === 'waiting') return 'amber';
    if (columnKey === 'needs') return 'red';
    return null;
  }

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
        shortId: compactRef(chunk.chunk_id),
        status: chunk.status,
        node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
        nodeId: chunk.current_node_id ?? '',
        // Only labeled pointers show — a pointer naming no configured source has a
        // null label and the card leans on the short id instead.
        pointerLabel: (chunk.pm_pointers ?? [])
          .flatMap((p) => (p.label ? [p.label] : []))
          .join(' '),
        costUsd: chunk.cost?.cost_usd ?? 0,
        costPartial: chunk.cost?.cost_partial ?? false,
      });
    }
    return grouped;
  });

  protected readonly total = computed(() => this.chunks().length);

  protected cardsFor(columnKey: string): readonly BoardCard[] {
    return this.cards().get(columnKey) ?? [];
  }
}

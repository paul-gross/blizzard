import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { ChunkStatus } from '../api/hub';
import { STATUS_LANE } from '../chunk-lanes';
import { formatCost } from '../cost-format';
import { FleetWhen } from '../when-display';

/** One rendered board card — the derived-status view of a chunk. */
export interface BoardCard {
  readonly chunkId: string;
  readonly shortId: string;
  readonly status: ChunkStatus;
  /** The node's human graph name (`build`, `review`); falls back to the raw id. */
  readonly node: string;
  /** The raw `nd_` ULID, kept reachable as the node label's tooltip. */
  readonly nodeId: string;
  /** The chunk's work items — each entry the server-derived `{source}#{ref}`
   * label for one pointer, empty when no pointer names a configured source.
   * Rendered one per line, not joined. */
  readonly pointerLabels: readonly string[];
  /** The chunk's derived spend total (issue #60), from `ChunkSummary.cost`. */
  readonly costUsd: number;
  /** Whether {@link costUsd} is a lower bound — a summed invocation's envelope-less
   * cost was absent (crash/reap path); never presented as exact. */
  readonly costPartial: boolean;
  /** The chunk's derived completion instant (issue #173), from `ChunkSummary.completed_at`
   * — null for every non-terminal status. Rendered only on a done-lane card
   * ({@link BoardCardComponent.isDoneLane}): a status this field's own null-ness doesn't
   * already rule out, but the lane a defensive belt-and-suspenders check still asks for. */
  readonly completedAt: string | null;
}

/**
 * One board card — split out of {@link BoardShell} (issue #137) so that file
 * stayed under the `web:structural-gate` line cap as the READY lane's
 * drag-and-drop landed on it. Renders exactly what was inline in
 * `BoardShell`'s `@for` before, plus one addition — `[attr.data-chunk]`
 * carries the card's full chunk id, a unique locator the e2e suite needs
 * since same-instant chunk ids share a 12-char prefix. Nothing else in the
 * board repeats that attribute, including the READY lane's own per-card
 * queue controls, so it stays one node per chunk.
 *
 * The same card renders in every lane, READY included: the queue-shaping
 * affordances that lane adds are {@link BoardColumn}'s, rendered beside the
 * card rather than inside it, so this stays the one card the whole board is
 * built from.
 *
 * Presentational only: {@link card} and {@link selected} are plain inputs, and
 * both outputs forward the chunk id verbatim to whatever container composes
 * this — no query or mutation injected here
 * (`bzh:frontend-container-presentational`).
 *
 * Named `BoardCardComponent` rather than this directory's usual bare-name
 * convention (`BoardShell`, `BoardHeader`, …): {@link BoardCard} — the
 * per-card view type `BoardShell` derives and this file now owns — already
 * carries the plain name, so the component takes the suffix instead of
 * colliding with it.
 */
@Component({
  selector: 'fleet-board-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen],
  template: `
    <div
      class="card"
      data-testid="chunk-card"
      [attr.data-status]="card().status"
      [attr.data-chunk]="card().chunkId"
      [class.selected]="selected()"
      [attr.aria-current]="selected() ? 'true' : null"
    >
      <button
        type="button"
        class="card-open"
        [attr.aria-label]="'Open chunk ' + card().shortId"
        (click)="selectChunk.emit(card().chunkId)"
      >
        <span class="tid">
          <span class="card-id" data-testid="chunk-id">{{ card().shortId }}</span>
          <span class="nd" data-testid="chunk-node" [attr.title]="card().nodeId || null">{{
            card().node
          }}</span>
        </span>
        <!-- Pointer labels are plain text here, not links: a card is a
             target for opening the chunk, and an anchor inside it competes
             for the same click. The detail panel owns the link out to the work source. -->
        <span class="iss-list">
          @for (label of card().pointerLabels; track label) {
            <span class="iss" data-testid="work-ref-chip" [title]="label">{{ label }}</span>
          }
        </span>
        <span class="st-row">
          <span class="st" data-testid="chunk-status" [title]="card().status">{{ card().status }}</span>
          @if (isDoneLane(card().status) && card().completedAt; as completedAt) {
            <fleet-when class="done-at" data-testid="chunk-done-at" [iso]="completedAt" />
          }
          @if (card().costUsd > 0 || card().costPartial) {
            <span class="cost" data-testid="card-cost">{{
              formatCost(card().costUsd, card().costPartial)
            }}</span>
          }
        </span>
      </button>
      @if (card().status === 'not_ready') {
        <button
          type="button"
          class="card-promote"
          data-testid="promote-chunk"
          [attr.aria-label]="'Promote chunk ' + card().shortId + ' to ready'"
          (click)="promote.emit(card().chunkId)"
        >
          PROMOTE
        </button>
      }
    </div>
  `,
  styles: `
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
    /* The DONE column's cards (issue #137: done and stopped, the two
       statuses STATUS_LANE folds onto it) keep their green accent, and an
       escalated card (needs_human) reads in the alarm color — both were
       column-scoped rules reaching down from BoardShell's own styles before
       this component owned its own status coloring. */
    .card[data-status='done'],
    .card[data-status='stopped'] {
      border-left-color: var(--green);
    }
    .card[data-status='needs_human'] {
      border-left-color: var(--red);
    }
    .card[data-status='needs_human'] .card-id {
      color: var(--red);
    }
    /* The BACKLOG card reads as held/inert: a dim accent, distinct from the
       READY lane beside it and from any live lane. Colors come from tokens,
       never hard-coded hex. */
    .card[data-status='not_ready'] {
      border-left-color: var(--label-dim);
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
    /* One label per line: a column, not the wrapping single line every other
       card row uses. Each line still clips on its own rather than wrapping
       (the file's standing rule, restated here since the container is new). */
    .iss-list {
      display: flex;
      flex-direction: column;
      min-width: 0;
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
    /* The done-lane completion stamp (issue #173) — dim like the status label it
       sits beside, not a separate accent; it is a timestamp, not a state. */
    .done-at {
      color: var(--label-dim);
      font-size: var(--fs-xs);
      white-space: nowrap;
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
  `,
})
export class BoardCardComponent {
  protected readonly formatCost = formatCost;

  /** The card's derived-status view of one chunk. */
  readonly card = input.required<BoardCard>();

  /** Whether the container considers this card the current selection — its
   * card carries the selection highlight so the board says which one is open. */
  readonly selected = input(false);

  /** Emitted with the chunk id when the card is activated — fills the detail dock. */
  readonly selectChunk = output<string>();

  /** Emitted with the chunk id when a not-ready card's Promote is clicked. */
  readonly promote = output<string>();

  /** Whether `status` belongs to the DONE column — the completion stamp's render
   * gate. Checked against the lane rather than {@link BoardCard.completedAt}'s own
   * null-ness alone, so a card outside the done lane never renders a stamp even if
   * it somehow carried one (issue #173). */
  protected isDoneLane(status: ChunkStatus): boolean {
    return STATUS_LANE[status] === 'done';
  }
}

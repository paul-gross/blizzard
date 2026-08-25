import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { ChunkStatus } from '../api/hub';
import { STATUS_LANE } from '../chunk-lanes';
import { formatCost } from '../cost-format';
import { KitButton } from '../kit/kit-button';
import { FleetWhen } from '../when-display';

/** Statuses with no acquiring runner — the only ones Delete reaches (D8, issue #364):
 * a `not_ready`/`ready` chunk has no live route to release, unlike every status
 * Detach guards. Mirrors {@link ChunkDetailHeader}'s `NOT_PAUSABLE`/`NOT_COMPLETABLE`
 * shape — a status set owned right beside the control it gates. */
const UNACQUIRED_STATUSES = new Set<ChunkStatus>(['not_ready', 'ready']);

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
 * Presentational only: {@link card} and {@link selected} are plain inputs; every
 * output forwards the chunk id to whatever container composes this — no query or
 * mutation injected here (`bzh:frontend-container-presentational`). {@link delete}
 * alone is gated by a `globalThis.confirm()` guard first ({@link onDelete}, D8,
 * issue #364), the same idiom `ChunkDetailHeader`'s route-releasing/terminal verbs
 * use — not a bespoke dialog, so the board keeps one confirmation idiom.
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
  imports: [FleetWhen, KitButton],
  templateUrl: './board-card.html',
  styleUrl: './board-card.css',
})
export class BoardCardComponent {
  protected readonly formatCost = formatCost;

  /** The card's derived-status view of one chunk. */
  readonly card = input.required<BoardCard>();

  /** Whether the container considers this card the current selection — its
   * card carries the selection highlight so the board says which one is open. */
  readonly selected = input(false);

  /** Whether the current identity may promote a backlog chunk (`chunk:control` —
   * issue #210). Withholds the Promote control when `false`; `null`/pending resolves
   * to `false` (hidden until confirmed). */
  readonly canControl = input(false);

  /** Emitted with the chunk id when the card is activated — fills the detail dock. */
  readonly selectChunk = output<string>();

  /** Emitted with the chunk id when a not-ready card's Promote is clicked. */
  readonly promote = output<string>();

  /** Emitted with the chunk id when an unacquired card's Delete is confirmed
   * (D8, issue #364). */
  readonly delete = output<string>();

  /** Whether `status` belongs to the DONE column — the completion stamp's render
   * gate. Checked against the lane rather than {@link BoardCard.completedAt}'s own
   * null-ness alone, so a card outside the done lane never renders a stamp even if
   * it somehow carried one (issue #173). */
  protected isDoneLane(status: ChunkStatus): boolean {
    return STATUS_LANE[status] === 'done';
  }

  /** The upper-right node slot's label — {@link BoardCard.node} for every status
   * except `stopped`, which shows the status word instead: a stopped chunk's
   * last-active node name (e.g. "deliver") read as unhelpful noise next to
   * "stopped" in the lower-left status label it used to sit beside (issue #215),
   * so it's replaced rather than shown alongside. */
  protected nodeLabel(card: BoardCard): string {
    return card.status === 'stopped' ? 'stopped' : card.node;
  }

  /** Whether `status` is unacquired ({@link UNACQUIRED_STATUSES}) — Delete's own
   * render gate (D8, issue #364). */
  protected isUnacquired(status: ChunkStatus): boolean {
    return UNACQUIRED_STATUSES.has(status);
  }

  /** Confirm, then emit `delete` for the container's mutation to fire (D8, issue
   * #364). Withdraws the chunk's hub item(s); there is no undo. */
  protected onDelete(card: BoardCard): void {
    const confirmed = globalThis.confirm(
      `Delete chunk ${card.chunkId}? This withdraws its hub item(s); there is no undo.`,
    );
    if (!confirmed) return;
    this.delete.emit(card.chunkId);
  }
}

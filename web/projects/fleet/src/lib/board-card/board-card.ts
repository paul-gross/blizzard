import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { ChunkStatus } from '../api/hub';
import { STATUS_LANE } from '../chunk-lanes';
import { compactRef } from '../compact-ref';
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
  /** The unmet prerequisite's chunk id, from `ChunkSummary.blocked` (issue #461) — null
   * for every card outside `not_ready`/`ready` (`blizzard-context:/domain/work/statuses.md`),
   * and for one inside it with no standing edge. Names the immediate prerequisite only. */
  readonly blockedOn: string | null;
  /** How many prerequisites are unmet in total, from `BlockedView.unmet_count` — 0 whenever
   * {@link blockedOn} is null. Above 1 the card counts them rather than naming the first and
   * silently dropping the rest; there is no room on a card for a list. */
  readonly blockedCount: number;
  /** The blocking chunk's own derived status, when exactly one chunk blocks this one —
   * null otherwise (nothing blocking, several blocking, or the blocker absent from the
   * board's own chunk list). Read off the board's existing chunk list, not the wire:
   * `BlockedView` names the prerequisite but not its status. */
  readonly blockedOnStatus: ChunkStatus | null;
}

/**
 * One board card — split out of {@link BoardShell} (issue #137) so that file
 * stayed under the `web:lint` line cap as the READY lane's
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
 * mutation injected here (`bzh:frontend-container-presentational`). Delete is not
 * among them: it lives on `ChunkDetailHeader` now, not here — a card this small
 * has no room for a control that invasive, and the dock already owns every other
 * route-releasing/terminal verb.
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

  /** What the blocked marking names, beside the status: the one unmet prerequisite's compact
   * ref, or a count once there is more than one. A card has no room to list them, and naming
   * only the first would read as the whole answer. */
  protected blockedLabel(card: BoardCard): string {
    return card.blockedCount > 1 ? `${card.blockedCount} chunks` : compactRef(card.blockedOn ?? '');
  }

  /** The blocker's own status, parenthesised beside its ref — only when exactly one
   * chunk blocks this one. A count ("2 chunks") names no single chunk, so there is no
   * one status to qualify it with. */
  protected blockedOnStatus(card: BoardCard): string | null {
    return card.blockedCount === 1 ? card.blockedOnStatus : null;
  }
}

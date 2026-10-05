import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { type ChunkStatus, type LandedRepoView, type PrView, STATUS_LANE, compactRef, formatCost, hasCostFigure, FleetWhen, KitBadge } from 'fleet';

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
  /** The chunk's derived spend total, from `ChunkSummary.cost`. */
  readonly costUsd: number;
  /** Whether {@link costUsd} is a lower bound, never presented as exact — `UsageTotal`'s
   * `cost_partial`. */
  readonly costPartial: boolean;
  /** The chunk's derived spend estimate, from `ChunkSummary.cost.estimated_cost_usd` —
   * `null` iff no summed row reported one. Already included in {@link costUsd}. */
  readonly estimatedCostUsd: number | null;
  /** The chunk's derived completion instant, from `ChunkSummary.completed_at`
   * — null for every non-terminal status. */
  readonly completedAt: string | null;
  /** The unmet prerequisite's chunk id, from `ChunkSummary.blocked` — null
   * for every card outside `not_ready`/`ready` (`blizzard-context:/domain/work/statuses.md`),
   * and for one inside it with no standing edge. Names the immediate prerequisite only. */
  readonly blockedOn: string | null;
  /** How many prerequisites are unmet in total, from `BlockedView.unmet_count` — 0 whenever
   * {@link blockedOn} is null. The count-vs-list choice above 1 is pinned by
   * `board-card.spec.ts`'s "counts the prerequisites instead of naming the first…" case. */
  readonly blockedCount: number;
  /** The blocking chunk's own derived status, when exactly one chunk blocks this one —
   * null otherwise (nothing blocking, several blocking, or the blocker absent from the
   * board's own chunk list). Derived from the board's own chunk list rather than carried
   * on the wire directly. */
  readonly blockedOnStatus: ChunkStatus | null;
  readonly openPrs?: readonly PrView[];
  readonly landedRepos?: readonly LandedRepoView[];
  readonly awaitingExternalMerge?: boolean;
}

/**
 * One board card for every lane. `[attr.data-chunk]`
 * carries the card's full chunk id, a unique locator the e2e suite needs
 * since same-instant chunk ids share a 12-char prefix. Nothing else in the
 * board repeats that attribute, so it stays one node per chunk.
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
  selector: 'app-board-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitBadge],
  templateUrl: './board-card.html',
  styleUrl: './board-card.css',
})
export class BoardCardComponent {
  protected readonly formatCost = formatCost;
  protected readonly hasCostFigure = hasCostFigure;

  /** The card's derived-status view of one chunk. */
  readonly card = input.required<BoardCard>();

  /** Whether the container considers this card the current selection — its
   * card carries the selection highlight so the board says which one is open. */
  readonly selected = input(false);

  /** Whether the chunk has an open decision awaiting a person — the card then
   * carries the `gate` marker. */
  readonly gated = input(false);

  /** Whether the current identity may promote a backlog chunk (`chunk:control`).
   * Withholds the Promote control when `false`; `null`/pending resolves
   * to `false` (hidden until confirmed). */
  readonly canControl = input(false);

  /** Emitted with the chunk id when the card is activated — fills the detail dock. */
  readonly selectChunk = output<string>();

  /** Emitted with the chunk id when a not-ready card's Promote is clicked. */
  readonly promote = output<string>();

  /** Whether `status` belongs to the DONE column — the completion stamp's render
   * gate. Checked against the lane rather than {@link BoardCard.completedAt}'s own
   * null-ness alone, so a card outside the done lane never renders a stamp even if
   * it somehow carried one. */
  protected isDoneLane(status: ChunkStatus): boolean {
    return STATUS_LANE[status] === 'done';
  }

  /** Whether the card renders its delivery block: only in the lanes where a chunk's delivery is
   * in flight (RUNNING, WAIT/HUMAN, NEEDS HUMAN), and only when there is an open PR to link or a
   * merge to await. Landed commits never show here — the chunk detail owns the full history. */
  protected showsDelivery(card: BoardCard): boolean {
    const lane = STATUS_LANE[card.status];
    return (
      (lane === 'running' || lane === 'waiting' || lane === 'needs') &&
      !!(card.openPrs?.length || card.awaitingExternalMerge)
    );
  }

  /** The upper-right node slot's label — {@link BoardCard.node} for every status
   * except `stopped`, which shows the status word instead: a stopped chunk's
   * last-active node name (e.g. "deliver") read as unhelpful noise next to
   * "stopped" in the lower-left status label it used to sit beside,
   * so it's replaced rather than shown alongside. */
  protected nodeLabel(card: BoardCard): string {
    return card.status === 'stopped' ? 'stopped' : card.node;
  }

  /** The node slot's hover text: the full label the card clips, with the raw node id
   * beside it when the label is a name rather than that id. */
  protected nodeTitle(card: BoardCard): string | null {
    const label = this.nodeLabel(card);
    return card.nodeId && card.nodeId !== label ? `${label} (${card.nodeId})` : label || null;
  }

  /** What the blocked marking names, beside the status: the one unmet prerequisite's compact
   * ref, or a count once there is more than one — pinned by `board-card.spec.ts`'s "counts the
   * prerequisites instead of naming the first…" case. */
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

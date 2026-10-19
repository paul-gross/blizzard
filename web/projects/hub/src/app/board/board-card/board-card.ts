import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import { projectColorValue, type ChunkStatus, type LandedRepoView, type PrView, STATUS_LANE, formatCost, hasCostFigure, FleetWhen, KitBadge, KitButton, KitSelectRow, KitSkeleton } from 'fleet';
import { blockedLabel, cardShowsStatus, foldTitles, stepLabel, type WorkLine } from './board-card.model';

/** One rendered board card — the derived-status view of a chunk. */
export interface BoardCard {
  readonly chunkId: string;
  readonly shortId: string;
  readonly status: ChunkStatus;
  /** The hub's `ChunkSummary.terminal` — the card sits in the DONE lane exactly when set. */
  readonly terminal: boolean;
  /** The hub's `ChunkSummary.promotable` — a promote would write a promotion; gates Promote. */
  readonly promotable: boolean;
  /** The node's human graph name (`build`, `review`); falls back to the raw id. */
  readonly node: string;
  /** The raw `nd_` ULID, kept reachable as the node label's tooltip. */
  readonly nodeId: string;
  /** The chunk's work items as label-and-title pairs, in pointer order (`workLines`). */
  readonly workLines: readonly WorkLine[];
  /** Whether the chunk's work items have been read — until then each title holds a skeleton. */
  readonly itemsRead: boolean;
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
  /** The blocking chunk's own derived status, when exactly one known chunk blocks this
   * one — null otherwise. */
  readonly blockedOnStatus: ChunkStatus | null;
  readonly openPrs?: readonly PrView[];
  readonly landedRepos?: readonly LandedRepoView[];
  readonly awaitingExternalMerge?: boolean;
  /** The chunk's project's display name, from `ChunkSummary.project_name`. */
  readonly projectName?: string | null;
  /** The chunk's project's palette color, joined from the projects list by `ChunkSummary.project_id` — `null` until
   * that list is read or when the project is unknown. */
  readonly projectColor?: string | null;
  /** The runner holding the chunk, from `ChunkSummary.runner_name` — null when none does. */
  readonly runnerName?: string | null;
  /** The host the holding runner runs on, joined from the host registry by `ChunkSummary.runner_id` — null when no
   * runner holds the chunk or its host is unknown. */
  readonly hostName?: string | null;
}

/**
 * One board card for every lane. `[attr.data-chunk]`
 * carries the card's full chunk id, a unique locator the e2e suite needs
 * since same-instant chunk ids share a 12-char prefix. Nothing else in the
 * board repeats that attribute, so it stays one node per chunk.
 *
 * Presentational only (`bzh:frontend-container-presentational`): every output
 * carries the chunk id.
 *
 * Suffixed `Component` because {@link BoardCard}, the view type, already carries
 * the plain name.
 */
@Component({
  selector: 'app-board-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitBadge, KitButton, KitSelectRow, KitSkeleton],
  templateUrl: './board-card.html',
  styleUrl: './board-card.css',
})
export class BoardCardComponent {
  protected readonly formatCost = formatCost;
  protected readonly hasCostFigure = hasCostFigure;

  /** The card's derived-status view of one chunk. */
  readonly card = input.required<BoardCard>();

  /** Whether this card is the current selection; it then carries the selection highlight. */
  readonly selected = input(false);

  /** Whether the chunk has an open decision awaiting a person — the card then
   * carries the `gate` marker. */
  readonly gated = input(false);

  /** Whether the current identity may promote a backlog chunk (`chunk:control`).
   * Withholds the Promote control when `false`; `null`/pending resolves
   * to `false` (hidden until confirmed). */
  readonly canControl = input(false);

  /** Emitted with the chunk id when the card is activated. */
  readonly selectChunk = output<string>();

  /** Emitted with the chunk id when a promotable card's Promote is clicked. */
  readonly promote = output<string>();

  /** The chunk's project color the card's background is tinted with, or `null` for an untinted card. */
  protected readonly projectTint = computed(() => {
    const color = this.card().projectColor;
    return color ? projectColorValue(color) : null;
  });

  /** Whether the card's work items are still unread — each title slot then holds a skeleton. */
  protected readonly titlesPending = computed(() => !this.card().itemsRead);

  /** The card's work items, each a label over its title. */
  protected readonly lines = computed(() => this.card().workLines);

  /** Whether a folded group has been opened to show every item. */
  protected readonly expanded = signal(false);

  /** The items the card shows, and how many its fold hides. */
  protected readonly fold = computed(() => foldTitles(this.lines(), this.expanded()));

  protected toggleFold(): void {
    this.expanded.update((open) => !open);
  }

  /** Opens the chunk on a click anywhere on the card but a control of its own — a link or
   * a button other than the open control beneath the rows — which acts for itself. */
  protected open(event: MouseEvent): void {
    const control = (event.target as Element).closest('a, button');
    if (control && !control.closest('.card-open')) return;
    this.selectChunk.emit(this.card().chunkId);
  }

  /** Whether the card belongs to the DONE column — the hub's `terminal` flag. */
  protected isDoneLane(card: BoardCard): boolean {
    return card.terminal;
  }

  /** The completion stamp's instant: a done-lane card's {@link BoardCard.completedAt}.
   * Gated on the lane rather than the instant's own null-ness alone, so a card outside the
   * done lane never renders a stamp even if it somehow carried one. */
  protected completedStamp(card: BoardCard): string | null {
    return this.isDoneLane(card) ? card.completedAt : null;
  }

  /** Whether the card renders its delivery block: only in the lanes where a chunk's delivery is
   * in flight (RUNNING, WAIT/HUMAN, NEEDS HUMAN), and only when there is an open PR to link or a
   * merge to await. Landed commits never show here — the chunk detail owns the full history. */
  protected showsDelivery(card: BoardCard): boolean {
    const lane = STATUS_LANE[card.status];
    return (
      !card.terminal &&
      (lane === 'running' || lane === 'waiting' || lane === 'needs') &&
      !!(card.openPrs?.length || card.awaitingExternalMerge)
    );
  }

  /** Whether the status word renders: never in the DONE lane (the step slot already says
   * it), and elsewhere only where it says more than the lane's own heading. */
  protected showsStatus(card: BoardCard): boolean {
    return !this.isDoneLane(card) && cardShowsStatus(card.status);
  }

  /** The step slot's label — {@link BoardCard.node} for every status
   * except `stopped`, which shows the status word instead: a stopped chunk's
   * last-active node name (e.g. "deliver") is unhelpful noise once the chunk
   * has stopped, so it's replaced rather than shown alongside. */
  protected nodeLabel(card: BoardCard): string {
    return stepLabel(card.status, card.node);
  }

  /** The step slot's hover text: the full label the card clips, with the raw node id
   * beside it when the label is a name rather than that id. */
  protected nodeTitle(card: BoardCard): string | null {
    const label = this.nodeLabel(card);
    return card.nodeId && card.nodeId !== label ? `${label} (${card.nodeId})` : label || null;
  }

  /** What the blocked marking names: the one unmet prerequisite's compact
   * ref, or a count once there is more than one — pinned by `board-card.spec.ts`'s "counts the
   * prerequisites instead of naming the first…" case. */
  protected blockedLabel(card: BoardCard): string {
    return blockedLabel(card.blockedOn, card.blockedCount);
  }

  /** The blocker's own status, parenthesised beside its ref — only when exactly one
   * chunk blocks this one. A count ("2 chunks") names no single chunk, so there is no
   * one status to qualify it with. */
  protected blockedOnStatus(card: BoardCard): string | null {
    return card.blockedCount === 1 ? card.blockedOnStatus : null;
  }
}

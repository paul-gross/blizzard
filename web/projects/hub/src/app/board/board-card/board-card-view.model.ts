import {
  blockedLabel,
  cardShowsStatus,
  type ChunkCardView,
  type ChunkStatus,
  finishedWord,
  formatCost,
  hasCostFigure,
  type LandedRepoView,
  type PrView,
  runnerBoardParts,
  runnerTitle,
  STATUS_LANE,
  STATUS_TONE,
  stepLabel,
  type WorkLine,
} from 'fleet';
import type { FlowPlaceLine } from '../flows/flow-place.model';

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
   * `board-card-view.spec.ts`'s "counts the prerequisites instead of naming the first…" case. */
  readonly blockedCount: number;
  /** The blocking chunk's own derived status, when exactly one known chunk blocks this
   * one — null otherwise. */
  readonly blockedOnStatus: ChunkStatus | null;
  readonly openPrs?: readonly PrView[];
  readonly landedRepos?: readonly LandedRepoView[];
  readonly awaitingExternalMerge?: boolean;
  /** The chunk's place on each flow its open submission holds, from `ChunkSummary.flow_places` — one line per flow,
   * `landing on blizzard/master` or `3rd on blizzard/master`. */
  readonly flowPlaces?: readonly FlowPlaceLine[];
  /** The chunk's project's display name, from `ChunkSummary.project_name`. */
  readonly projectName?: string | null;
  /** The chunk's project's palette color, joined from the projects list by `ChunkSummary.project_id` — `null` until
   * that list is read or when the project is unknown. */
  readonly projectColor?: string | null;
  /** The runner holding the chunk, from `ChunkSummary.runner_id` — null when none does. */
  readonly runnerId?: string | null;
  /** The runner holding the chunk's name, from `ChunkSummary.runner_name` — null when none holds it or it is unnamed. */
  readonly runnerName?: string | null;
  /** The host the holding runner runs on, joined from the host registry by `ChunkSummary.runner_id` — null when no
   * runner holds the chunk or its host is unknown. */
  readonly hostName?: string | null;
}

/** Whether the card draws its delivery block: only in the lanes where a chunk's delivery is in flight (RUNNING,
 * WAIT/HUMAN, NEEDS HUMAN), and only when there is an open PR to link, a merge to await, or a place on a flow. Landed
 * commits never show here — the chunk detail owns the full history. */
function showsDelivery(card: BoardCard): boolean {
  const lane = STATUS_LANE(card.status);
  return (
    !card.terminal &&
    (lane === 'running' || lane === 'waiting' || lane === 'needs') &&
    !!(card.openPrs?.length || card.awaitingExternalMerge || card.flowPlaces?.length)
  );
}

/** The step's hover text: the full label the card clips, with the raw node id beside it when the label is a name
 * rather than that id. */
function stepTitle(label: string, nodeId: string): string | null {
  return nodeId && nodeId !== label ? `${label} (${nodeId})` : label || null;
}

/**
 * A board card as the fleet card and list row draw it. Its tone is the status's (`STATUS_TONE`). The status word is
 * said only where it says more than the lane — never in the DONE lane, whose step already says it; a stopped chunk's
 * step is the status word, its last node being noise once it has stopped. The completion stamp is a done-lane card's
 * alone, and the blocked marking names the blocker's own status only when exactly one chunk blocks this one — a count
 * names no single chunk.
 */
export function boardCardView(card: BoardCard): ChunkCardView {
  const step = stepLabel(card.status, card.node);
  const { runnerId, runnerName, hostName } = card;
  return {
    chunkId: card.chunkId,
    shortId: card.shortId,
    status: card.status,
    tone: STATUS_TONE(card.status),
    statusWord: !card.terminal && cardShowsStatus(card.status) ? card.status : null,
    step,
    stepTitle: stepTitle(step, card.nodeId),
    workLines: card.workLines,
    itemsRead: card.itemsRead,
    holder: runnerId ? { ...runnerBoardParts(runnerId, runnerName, hostName), title: runnerTitle(runnerId, runnerName, hostName) } : null,
    blocked: card.blockedOn
      ? {
          label: blockedLabel(card.blockedOn, card.blockedCount),
          counted: card.blockedCount > 1,
          title: card.blockedOn,
          state: card.blockedCount === 1 ? card.blockedOnStatus : null,
        }
      : null,
    showsDelivery: showsDelivery(card),
    places: (card.flowPlaces ?? []).map((place) => ({ key: place.flowId, text: place.text, tone: place.tone, title: place.flowId })),
    awaitingExternalMerge: card.awaitingExternalMerge,
    openPrs: card.openPrs,
    landedRepos: card.landedRepos,
    cost: hasCostFigure(card.costUsd, card.estimatedCostUsd, card.costPartial)
      ? formatCost(card.costUsd, card.estimatedCostUsd, card.costPartial)
      : null,
    completedAt: card.terminal ? card.completedAt : null,
    finishedWord: finishedWord(card.status),
    promotable: card.promotable,
    projectName: card.projectName,
    projectColor: card.projectColor,
  };
}

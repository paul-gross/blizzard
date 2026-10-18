import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { type ChunkCountsView, type ChunkSummary, type WorkItemsView, chunkLane, compactRef, DONE_LANE, LANES, laneCounts, KitAsyncState, type KitAsyncStateValue, KitPanel, KitPanelHeader, KitSkeleton, KitTabs, type KitTabOption } from 'fleet';
import type { BoardCard } from '../board-card/board-card';
import { workItemDescription, workItemTitles } from '../board-card/board-card.model';
import { BoardColumn, type BoardLaneMove, type BoardReposition } from './board-column';
import { BoardList, type BoardListSection } from '../board-list/board-list';

export type { BoardCard, BoardLaneMove, BoardReposition };

/** How the board lays out its chunks: the lane grid, or the lanes as a list of sections. */
export type BoardLayout = 'board' | 'list';

const LAYOUT_TABS: readonly KitTabOption[] = [
  { value: 'board', label: 'Board', testid: 'board-layout-board' },
  { value: 'list', label: 'List', testid: 'board-layout-list' },
];

/** The list's section order — what wants a person first, then the pipeline back to front,
 * DONE last. The grid keeps the pipeline's own left-to-right order. */
const LIST_ORDER: readonly string[] = ['needs', 'waiting', 'running', 'ready', 'notready', DONE_LANE];

/**
 * The mission-control chunk board — the six status columns and their
 * cards.
 *
 * READY and BACKLOG are two of those columns: both are hub-ranked
 * lists (`bzh:ranking-is-per-list`), so each renders top-to-bottom in its own
 * hub order ({@link readyOrder}/{@link backlogOrder}) and is reshaped in place:
 * card drags leave as {@link reposition}, lane-tagged.
 *
 * Presentational only: it holds no data client.
 */
@Component({
  selector: 'app-board-shell',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardColumn, BoardList, KitPanel, KitPanelHeader, KitAsyncState, KitSkeleton, KitTabs],
  templateUrl: './board-shell.html',
  styleUrl: './board-shell.css',
})
export class BoardShell {
  /** The fleet chunk list (derived status + current node); empty when the fleet is idle. */
  readonly chunks = input<readonly ChunkSummary[]>([]);

  /** The all-time fleet counts each column head shows — `null` while the read is
   * pending, when the heads withhold their numbers. */
  readonly counts = input<ChunkCountsView | null>(null);

  /**
   * The ready chunk ids in hub dispatch order. The READY lane renders in exactly this order; a ready
   * chunk the order does not name sorts after the ones it does, keeping its relative order, rather
   * than jumping the queue or vanishing.
   */
  readonly readyOrder = input<readonly string[]>([]);

  /**
   * The `not_ready` chunk ids in the hub's own backlog order — the BACKLOG
   * lane's ranking, independent of {@link readyOrder} (`bzh:ranking-is-per-list`).
   * Renders and sorts exactly like {@link readyOrder}: an unranked id sorts after the ones the order
   * does name, keeping its relative order, rather than jumping the list or
   * vanishing.
   */
  readonly backlogOrder = input<readonly string[]>([]);

  /** The chunk list's async state. */
  readonly state = input.required<KitAsyncStateValue>();

  /** Emitted with a chunk id when its card is activated. */
  readonly selectChunk = output<string>();

  /** The selected chunk, or null — its card carries the selection highlight. */
  readonly selectedChunkId = input<string | null>(null);

  /** Every chunk id with an open decision, forwarded to every {@link BoardColumn}. */
  readonly gatedChunkIds = input<ReadonlySet<string>>(new Set());

  /** Emitted with a chunk id when a backlog card's Promote is clicked. */
  readonly promote = output<string>();

  /** Emitted when a READY or BACKLOG card is dragged somewhere new — the chunk,
   * the anchor it now sits after (`null` = the very top of that list), and which
   * list it belongs to (forwarded as-is from {@link BoardColumn}). */
  readonly reposition = output<BoardReposition>();

  /** Emitted when a card is dropped into the other ranked list — a demote out of READY or a
   * promote out of BACKLOG, with the destination anchor (forwarded as-is from {@link BoardColumn}). */
  readonly laneMove = output<BoardLaneMove>();

  /** Whether the current identity may promote a backlog chunk (`chunk:control`),
   * forwarded to every {@link BoardColumn}. `null`/pending resolves to
   * `false` (hidden until confirmed). */
  readonly canControl = input(false);

  /** Whether the current identity may reorder the ready queue or backlog
   * (`queue:reorder`), forwarded to the ranked {@link BoardColumn}s. */
  readonly canReorder = input(false);

  /** Every chunk's work items read so far, by chunk id — a card leads with their titles
   * once its chunk's arrive, and keeps its untitled layout until then. */
  readonly workItems = input<ReadonlyMap<string, WorkItemsView>>(new Map());

  /** Which layout renders — the lane grid or the list. */
  readonly layout = input<BoardLayout>('board');

  /** Emitted with the layout the switch was set to. */
  readonly layoutChange = output<BoardLayout>();

  protected readonly columns = LANES;
  protected readonly layoutTabs = LAYOUT_TABS;

  /** Every lane as a list section, in {@link LIST_ORDER}, each holding the same cards in the
   * same order its grid column does. */
  protected readonly sections = computed<readonly BoardListSection[]>(() =>
    LIST_ORDER.flatMap((key) => {
      const lane = LANES.find((l) => l.key === key);
      return lane ? [{ key, label: lane.headerLabel, count: this.countFor(key), cards: this.cardsFor(key) }] : [];
    }),
  );

  protected chooseLayout(value: string): void {
    this.layoutChange.emit(value === 'list' ? 'list' : 'board');
  }

  /** Every chunk rendered as a board card, grouped into its status column. */
  private readonly cards = computed<Map<string, BoardCard[]>>(() => {
    const grouped = new Map<string, BoardCard[]>(LANES.map((lane) => [lane.key, []]));
    // Every chunk's status by id, so a blocked card can name its blocker's state without
    // a second read — the blocker is itself a chunk on this board.
    const statusById = new Map(this.chunks().map((c) => [c.chunk_id, c.status]));
    for (const chunk of this.chunks()) {
      const workItems = this.workItems().get(chunk.chunk_id);
      grouped.get(chunkLane(chunk))?.push({
        chunkId: chunk.chunk_id,
        shortId: compactRef(chunk.chunk_id),
        status: chunk.status,
        terminal: chunk.terminal ?? false,
        promotable: chunk.promotable ?? false,
        node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
        nodeId: chunk.current_node_id ?? '',
        // Only labeled pointers show — a pointer naming no configured source has a
        // null label and the card leans on the short id instead.
        pointerLabels: (chunk.work_refs ?? []).flatMap((p) => (p.label ? [p.label] : [])),
        costUsd: chunk.cost?.cost_usd ?? 0,
        costPartial: chunk.cost?.cost_partial ?? false,
        estimatedCostUsd: chunk.cost?.estimated_cost_usd ?? null,
        completedAt: chunk.completed_at ?? null,
        blockedOn: chunk.blocked?.prerequisite_chunk_id ?? null,
        // `unmet_count` is optional on the wire (it defaults server-side), so an older
        // payload carrying a marking without one still counts as the one it names.
        blockedCount: chunk.blocked ? (chunk.blocked.unmet_count ?? 1) : 0,
        // The blocker's status comes from the board's own chunk list — every chunk on
        // the board is in it, so no extra read, and a blocker somehow absent from it
        // degrades to naming the ref alone.
        blockedOnStatus: chunk.blocked ? (statusById.get(chunk.blocked.prerequisite_chunk_id) ?? null) : null,
        openPrs: chunk.open_prs ?? [],
        landedRepos: chunk.landed_repos ?? [],
        awaitingExternalMerge: chunk.awaiting_external_merge ?? false,
        projectName: chunk.project_name ?? null,
        runnerName: chunk.runner_name ?? null,
        titles: workItemTitles(workItems),
        description: workItemDescription(workItems),
      });
    }
    // READY is ordered rather than listed: it is a queue, so its rank comes
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
    // BACKLOG is ordered the same way, off its own hub order — the two lists
    // rank independently (`bzh:ranking-is-per-list`), so this is a second,
    // separate rank map rather than a shared one keyed differently.
    const backlogRank = new Map(this.backlogOrder().map((chunkId, index) => [chunkId, index]));
    const backlogRankOf = (card: BoardCard): number => backlogRank.get(card.chunkId) ?? Number.POSITIVE_INFINITY;
    grouped.get('notready')?.sort((a, b) => {
      const [left, right] = [backlogRankOf(a), backlogRankOf(b)];
      if (left === right) return 0;
      return left < right ? -1 : 1;
    });
    // DONE alone is ordered newest-first — a second lane-scoped
    // ordering, same shape as READY's above. A `null` completedAt (shouldn't happen
    // for a done-lane card, but not fabricated if it does) sorts last rather than
    // jumping to the top, and ties keep their relative order (stable sort).
    const completedRank = (card: BoardCard): number => {
      const ms = card.completedAt === null ? null : Date.parse(card.completedAt);
      return ms === null || Number.isNaN(ms) ? Number.NEGATIVE_INFINITY : ms;
    };
    grouped.get(DONE_LANE)?.sort((a, b) => {
      const [left, right] = [completedRank(a), completedRank(b)];
      if (left === right) return 0;
      return left > right ? -1 : 1;
    });
    return grouped;
  });

  private readonly laneTotals = computed(() => {
    const counts = this.counts();
    return counts === null ? null : laneCounts(counts);
  });

  protected countFor(columnKey: string): number | null {
    return this.laneTotals()?.get(columnKey) ?? null;
  }

  protected cardsFor(columnKey: string): readonly BoardCard[] {
    return this.cards().get(columnKey) ?? [];
  }
}

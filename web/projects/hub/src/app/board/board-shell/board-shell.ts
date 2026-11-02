import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { type ChunkCountsView, type ChunkSummary, type WorkItemsView, chunkLane, workLines, compactRef, DONE_LANE, LANES, laneCounts, UNKNOWN_LANE, unknownLane, KitAsyncState, type KitAsyncStateValue, KitPanel, KitPanelHeader, KitTabs, type KitTabOption } from 'fleet';
import type { BoardCard } from '../board-card/board-card-view.model';
import { placeLines } from '../flows/flow-place.model';
import { BoardColumn, type BoardLaneMove, type BoardReposition } from './board-column';
import { BoardList, type BoardListEntry, type BoardListGroups } from '../board-list/board-list';

export type { BoardCard, BoardLaneMove, BoardReposition };

/** The DONE window `listChunksApiChunksGet` keeps under `board_window`: chunks finished in the last
 * `BOARD_DONE_WINDOW_HOURS`, plus the newest `BOARD_DONE_FLOOR` however old. */
const BOARD_DONE_WINDOW_HOURS = 48;
const BOARD_DONE_FLOOR = 10;
/** The two sets the hub keeps are both newest-first, so their union is simply the larger one. */
const DONE_WINDOW_LABEL = `DONE MAX(${BOARD_DONE_WINDOW_HOURS}H, ${BOARD_DONE_FLOOR})`;

/** How the board lays out its chunks: the lane grid, or one list of full-width rows. */
export type BoardLayout = 'board' | 'list';

const LAYOUT_TABS: readonly KitTabOption[] = [
  { value: 'board', label: 'Board', testid: 'board-layout-board' },
  { value: 'list', label: 'List', testid: 'board-layout-list' },
];

/**
 * The mission-control chunk board — the six status columns and their
 * cards, plus a seventh, headed with the raw status, while any chunk carries a status this
 * build does not know (`unknownLane`).
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
  imports: [BoardColumn, BoardList, KitPanel, KitPanelHeader, KitAsyncState, KitTabs],
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

  /** Each project's palette color by project id, joined onto the cards — a project the map omits shows no swatch. */
  readonly projectColors = input<ReadonlyMap<string, string>>(new Map());

  /** Each runner's host name by runner id, joined onto the cards — a runner the map omits shows no host. */
  readonly runnerHosts = input<ReadonlyMap<string, string>>(new Map());

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
   * list it belongs to (forwarded as-is from {@link BoardColumn} or {@link BoardList}). */
  readonly reposition = output<BoardReposition>();

  /** Emitted when a card is dropped into the other ranked list — a demote out of READY or a
   * promote out of BACKLOG, with the destination anchor (forwarded as-is from {@link BoardColumn} or
   * {@link BoardList}). */
  readonly laneMove = output<BoardLaneMove>();

  /** Whether the current identity may promote a backlog chunk (`chunk:control`),
   * forwarded to every {@link BoardColumn}. `null`/pending resolves to
   * `false` (hidden until confirmed). */
  readonly canControl = input(false);

  /** Whether the current identity may reorder the ready queue or backlog
   * (`queue:reorder`), forwarded to the ranked {@link BoardColumn}s. */
  readonly canReorder = input(false);

  /** Every chunk's work items read so far, by chunk id — a card fills each work ref's title
   * once its chunk's arrive, and holds a skeleton in its place until then. */
  readonly workItems = input<ReadonlyMap<string, WorkItemsView>>(new Map());

  /** How many card skeletons each lane draws while the chunk read loads (`laneSkeletonCounts`). */
  readonly laneSkeletons = input<ReadonlyMap<string, number>>(new Map());

  /** How many row skeletons the list draws while the chunk read loads (`listSkeletonCount`). */
  readonly listSkeletons = input(0);

  /** Which layout renders — the lane grid or the list. */
  readonly layout = input<BoardLayout>('board');

  /** Emitted with the layout the switch was set to. */
  readonly layoutChange = output<BoardLayout>();

  protected readonly columns = computed(() => {
    const unknown = unknownLane(this.chunks());
    const lanes = LANES.map((lane) => (lane.key === DONE_LANE ? { ...lane, label: DONE_WINDOW_LABEL } : lane));
    return unknown === null ? lanes : [...lanes, unknown];
  });

  protected skeletonsFor(columnKey: string): number {
    return this.state() === 'loading' ? (this.laneSkeletons().get(columnKey) ?? 0) : 0;
  }
  protected readonly layoutTabs = LAYOUT_TABS;

  /** The list's rows, each tagged with its lane: what wants a person, then work in flight —
   * each lane newest first — then any chunk whose status this build does not know, then READY
   * and BACKLOG in their hub order, then DONE, newest finished first. Every group holds the same
   * cards in the same order its grid column does. */
  protected readonly listGroups = computed<BoardListGroups>(() => {
    const tagged = (key: string): readonly BoardListEntry[] => this.cardsFor(key).map((card) => ({ lane: key, card }));
    return {
      live: [...tagged('needs'), ...tagged('waiting'), ...tagged('running'), ...tagged(UNKNOWN_LANE)],
      ready: tagged('ready'),
      backlog: tagged('notready'),
      done: tagged(DONE_LANE),
    };
  });

  protected chooseLayout(value: string): void {
    this.layoutChange.emit(value === 'list' ? 'list' : 'board');
  }

  /** Every chunk rendered as a board card, grouped into its status column. */
  private readonly cards = computed<Map<string, BoardCard[]>>(() => {
    const grouped = new Map<string, BoardCard[]>(this.columns().map((lane) => [lane.key, []]));
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
        flowPlaces: placeLines(chunk.flow_places),
        projectName: chunk.project_name ?? null,
        projectColor: this.projectColors().get(chunk.project_id ?? '') ?? null,
        runnerId: chunk.runner_id ?? null,
        runnerName: chunk.runner_name ?? null,
        hostName: this.runnerHosts().get(chunk.runner_id ?? '') ?? null,
        workLines: workLines(chunk.work_refs ?? [], workItems),
        itemsRead: workItems !== undefined,
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

  /** A lane's all-time count. DONE and the unknown lane count the chunks they show instead: the board
   * holds only DONE's recent window, and no count covers the unknown lane. */
  protected countFor(columnKey: string): number | null {
    if (columnKey === UNKNOWN_LANE) return this.cardsFor(columnKey).length;
    if (columnKey === DONE_LANE) return this.state() === 'loading' ? null : this.cardsFor(columnKey).length;
    return this.laneTotals()?.get(columnKey) ?? null;
  }

  /** A column head's hover text — DONE's says what its window keeps, and its all-time total. */
  protected headTitleFor(columnKey: string): string | null {
    if (columnKey !== DONE_LANE) return null;
    const total = this.laneTotals()?.get(DONE_LANE);
    return `Whichever is more: everything finished in the last ${BOARD_DONE_WINDOW_HOURS}h, or the ${BOARD_DONE_FLOOR} most recent` + (total === undefined ? '' : ` · ${total} all time`);
  }

  protected cardsFor(columnKey: string): readonly BoardCard[] {
    return this.cards().get(columnKey) ?? [];
  }
}

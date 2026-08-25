import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import {
  BoardShell,
  type BoardReposition,
  type BoardTopMove,
  ChunkDetail,
  EventLogPanel,
  QuestionsPanel,
  RunnerPanel,
  asyncState,
  hasPermission,
  type KitAsyncStateValue,
  injectDeleteChunkMutation,
  injectGroupChunksMutation,
  injectHubBacklogQuery,
  injectHubChunksQuery,
  injectHubQueueQuery,
  injectMeQuery,
  injectPromoteChunkMutation,
  injectRepositionBacklogMutation,
  injectRepositionQueueMutation,
} from 'fleet';

import { injectBoardSelection } from './board-selection';

/**
 * The board route — the two-column mission-control surface:
 *
 * - the **centre** stacks {@link BoardShell} — every chunk in its derived-status
 *   column, the ready queue and the backlog among them as the READY and BACKLOG
 *   lanes — over the {@link ChunkDetail} dock. The dock is always mounted:
 *   selecting a card fills it (the work item, node history, artifacts, and the
 *   human-loop actions) and deselecting clears it to a rest state, so the board
 *   never resizes or reflows;
 * - the **right rail** holds {@link RunnerPanel}, the registry with pause/resume
 *   (MVP criterion 11), then {@link QuestionsPanel}, the fleet's open agent asks —
 *   clicking one opens its chunk in the dock, where it is answered — then
 *   {@link EventLogPanel}'s live feed.
 *
 * The left rail that used to hold the ready queue over the event log is gone
 * (issue #137): queue shaping — prioritize and
 * group — happens on the READY lane itself, so a ready chunk is a board card
 * like every other chunk instead of a row in a second surface. BACKLOG reorders
 * the same way (its own follow-up work), minus grouping — that stays READY-only.
 * This page owns the writes those affordances imply, since {@link BoardShell} is
 * presentational: the queue and backlog reads feed each lane's order, and the
 * lane-tagged reposition/Top events route to `POST /api/queue/position` or
 * `POST /api/backlog/position` (`bzh:ranking-is-per-list`); the group merge is
 * READY's alone.
 *
 * The titlebar, the {@link FleetLiveUpdates} spine, and the TanStack `QueryClient`
 * stay at the app root — none of them move here, so navigating away from and back
 * to `/board` never restarts the SSE stream or drops the query cache.
 *
 * Which card is open is **the URL's**, not this component's: `?chunk=…` on
 * `/board`, read and written through {@link injectBoardSelection} (issue #162,
 * the contract issue #99 set for the runner's local panel). Both selection
 * sources — a board card and an ask in the right rail — write the same param,
 * so a board is shareable, a reload keeps its place, and back/forward walk the
 * selection history.
 */
@Component({
  selector: 'app-board-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardShell, ChunkDetail, EventLogPanel, QuestionsPanel, RunnerPanel],
  templateUrl: './board-page.html',
  styleUrl: './board-page.css',
})
export class BoardPage {
  private readonly chunksQuery = injectHubChunksQuery();
  private readonly queueQuery = injectHubQueueQuery();
  private readonly repositionQueue = injectRepositionQueueMutation();
  private readonly repositionBacklog = injectRepositionBacklogMutation();
  private readonly groupChunks = injectGroupChunksMutation();
  private readonly selection = injectBoardSelection();
  private readonly meQuery = injectMeQuery();

  /** Whether the current identity may promote a backlog chunk (`chunk:control` —
   * issue #210). Withholds the board card's Promote control when `false`; `null`/pending
   * resolves to `false` (hidden until confirmed), the same convention `RunnerPanel`'s
   * `canPause` set. */
  protected readonly canControl = computed(() => hasPermission(this.meQuery.data(), 'chunk:control'));

  /** Whether the current identity may reorder the ready queue or backlog, or
   * group the ready queue (`queue:reorder` — issue #210). Withholds the READY
   * lane's drag-and-drop, Top button, checkbox, and Group control, and the
   * BACKLOG lane's drag-and-drop and Top button, when `false` — a read-only
   * board must not *arm* a drag it would then refuse, not merely hide a button.
   * Declared before {@link backlogQuery} (field initialization order), since
   * that query's `enabled` gate reads it directly. */
  protected readonly canReorder = computed(() => hasPermission(this.meQuery.data(), 'queue:reorder'));

  /** The backlog's own hub-ordered read (`GET /api/backlog`) — the BACKLOG
   * lane's ranking. Gated on {@link canReorder} itself, not merely rendered
   * conditionally: a board without `queue:reorder` must never even attempt this
   * read — the backlog is an operator triage surface, gated narrower than the
   * ready queue's `FLEET_VIEW`. */
  private readonly backlogQuery = injectHubBacklogQuery(this.canReorder);

  /** Promote a backlog chunk to ready from its board card. */
  protected readonly promoteChunk = injectPromoteChunkMutation();

  /** Delete an unacquired chunk from its board card (D8, issue #364). */
  protected readonly deleteChunk = injectDeleteChunkMutation();

  /** The live fleet chunk list; empty until the first read resolves. */
  protected readonly chunks = computed(() => this.chunksQuery.data() ?? []);

  /** The board's async state (AC 1, AC 2) — derived from the chunks query
   * alone: the queue read only supplies the READY lane's order, so it never
   * gates the board's emptiness. */
  protected readonly boardState = computed<KitAsyncStateValue>(() =>
    asyncState(this.chunksQuery, this.chunks().length === 0),
  );

  /**
   * The ready queue in the hub's own dispatch order, as bare ids — the READY
   * lane's ordering. It comes from `GET /api/queue` rather than from the fleet
   * list, because order is the queue's fact and the chunk list carries no rank.
   */
  protected readonly readyOrder = computed<readonly string[]>(() =>
    (this.queueQuery.data() ?? []).map((entry) => entry.chunk_id),
  );

  /**
   * The backlog in the hub's own order, as bare ids — the BACKLOG lane's
   * ordering, ranked independently of {@link readyOrder}
   * (`bzh:ranking-is-per-list`). Empty (never fetched) without `queue:reorder` —
   * {@link backlogQuery}'s own `enabled` gate, not a fallback here.
   */
  protected readonly backlogOrder = computed<readonly string[]>(() =>
    (this.backlogQuery.data() ?? []).map((entry) => entry.chunk_id),
  );

  /** A READY or BACKLOG card dropped somewhere new — placed after the anchor it
   * landed on (`null` = the very top), routed to the matching list's mutation. */
  protected reposition(move: BoardReposition): void {
    const mutation = move.list === 'notready' ? this.repositionBacklog : this.repositionQueue;
    mutation.mutate({ chunkId: move.chunkId, afterChunkId: move.afterChunkId });
  }

  /** A READY or BACKLOG card's Top button — the same reposition with no anchor,
   * routed to the matching list's mutation. */
  protected moveToTop(move: BoardTopMove): void {
    const mutation = move.list === 'notready' ? this.repositionBacklog : this.repositionQueue;
    mutation.mutate({ chunkId: move.chunkId, afterChunkId: null });
  }

  /** `ids` is the READY lane's multi-selection in lane order (the top-most is
   * the group survivor) — the lane owns the checkbox state itself, since it is
   * plain UI state, not query-derived. */
  protected group(ids: readonly string[]): void {
    if (ids.length < 2) return;
    const [survivorId, ...mergeChunkIds] = ids;
    this.groupChunks.mutate({ survivorId, mergeChunkIds });
  }

  /**
   * The board card the operator opened, or `null` when nothing is selected —
   * read from the URL (issue #162), never from local state.
   *
   * Held to the live fleet list, which `GET /api/chunks` returns whole: a
   * `chunk` param naming a chunk that no longer exists (or one that has not
   * arrived yet, on the first frame before the read resolves) reads as
   * no-selection, so the dock shows its normal rest state instead of chasing a
   * detail that will 404. The param itself is left alone — the board never
   * rewrites the URL to "correct" it, so a link that is merely early still
   * opens its chunk the moment the list lands.
   */
  protected readonly selected = computed<string | null>(() => {
    const chunkId = this.selection.chunkId();
    if (chunkId === null) return null;
    return this.chunks().some((chunk) => chunk.chunk_id === chunkId) ? chunkId : null;
  });

  /** Open a chunk in the dock — or clear it — by writing the URL. */
  protected select(chunkId: string | null): void {
    this.selection.select(chunkId);
  }
}

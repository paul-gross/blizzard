import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import {
  BoardShell,
  type BoardReposition,
  ChunkDetail,
  EventLogPanel,
  QuestionsPanel,
  RunnerPanel,
  asyncState,
  hasPermission,
  type KitAsyncStateValue,
  injectGroupChunksMutation,
  injectHubChunksQuery,
  injectHubQueueQuery,
  injectMeQuery,
  injectPromoteChunkMutation,
  injectRepositionQueueMutation,
} from 'fleet';

import { injectBoardSelection } from './board-selection';

/**
 * The board route — the two-column mission-control surface:
 *
 * - the **centre** stacks {@link BoardShell} — every chunk in its derived-status
 *   column, the ready queue among them as the READY lane — over the
 *   {@link ChunkDetail} dock. The dock is always mounted: selecting a card fills
 *   it (the work item, node history, artifacts, and the human-loop actions) and
 *   deselecting clears it to a rest state, so the board never resizes or reflows;
 * - the **right rail** holds {@link RunnerPanel}, the registry with pause/resume
 *   (MVP criterion 11), then {@link QuestionsPanel}, the fleet's open agent asks —
 *   clicking one opens its chunk in the dock, where it is answered — then
 *   {@link EventLogPanel}'s live feed.
 *
 * The left rail that used to hold the ready queue over the event log is gone
 * (issue #137): queue shaping — prioritize and
 * group — happens on the READY lane itself, so a ready chunk is a board card
 * like every other chunk instead of a row in a second surface. This page owns
 * the writes those affordances imply, since {@link BoardShell} is
 * presentational: the queue read feeds the lane's order, and its three outputs
 * drive `POST /api/queue/position` (drag and Top alike) and the group merge.
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
  private readonly groupChunks = injectGroupChunksMutation();
  private readonly selection = injectBoardSelection();
  private readonly meQuery = injectMeQuery();

  /** Promote a backlog chunk to ready from its board card. */
  protected readonly promoteChunk = injectPromoteChunkMutation();

  /** Whether the current identity may promote a backlog chunk (`chunk:control` —
   * issue #210). Withholds the board card's Promote control when `false`; `null`/pending
   * resolves to `false` (hidden until confirmed), the same convention `RunnerPanel`'s
   * `canPause` set. */
  protected readonly canControl = computed(() => hasPermission(this.meQuery.data(), 'chunk:control'));

  /** Whether the current identity may reorder or group the ready queue
   * (`queue:reorder` — issue #210). Withholds the READY lane's drag-and-drop, Top
   * button, checkbox, and Group control when `false` — a read-only board must not
   * *arm* a drag it would then refuse, not merely hide a button. */
  protected readonly canReorder = computed(() => hasPermission(this.meQuery.data(), 'queue:reorder'));

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

  /** A READY card dropped somewhere new — placed after the anchor it landed on
   * (`null` = the very top), which is what the hub route takes. */
  protected reposition(move: BoardReposition): void {
    this.repositionQueue.mutate({ chunkId: move.chunkId, afterChunkId: move.afterChunkId });
  }

  /** A READY card's Top button — the same reposition with no anchor. */
  protected moveToTop(chunkId: string): void {
    this.repositionQueue.mutate({ chunkId, afterChunkId: null });
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

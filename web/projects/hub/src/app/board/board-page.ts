import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import {
  BoardShell,
  type BoardReposition,
  type ChunkSummary,
  ChunkDetail,
  ActivityPanel,
  QuestionsPanel,
  RunnerPanel,
  asyncState,
  chunkDeleteMutationKey,
  errorMessage,
  hasPermission,
  injectChunkUrlSelection,
  type KitAsyncStateValue,
  type DeleteVars,
  injectHubBacklogQuery,
  injectHubChunksQuery,
  injectHubQueueQuery,
  injectMeQuery,
  injectPendingMutationVariables,
  injectPromoteChunkMutation,
  injectRepositionBacklogMutation,
  injectRepositionQueueMutation,
  promoteChunkMutationKey,
  type PromoteVars,
  type RepositionVars,
  repositionBacklogMutationKey,
  repositionQueueMutationKey,
} from 'fleet';

/**
 * A pending reposition's requested placement, replayed over a copy of `order` — `move.chunkId`
 * lands immediately after `move.afterChunkId`, or at the very top when that is `null`, mirroring
 * the anchor semantics `BoardColumn.dropped` computes when it emits a `BoardReposition`. `order`
 * itself is left untouched.
 */
function withRequestedPosition(order: readonly string[], move: RepositionVars): string[] {
  const withoutMoved = order.filter((id) => id !== move.chunkId);
  const afterIndex = move.afterChunkId === null ? -1 : withoutMoved.indexOf(move.afterChunkId);
  withoutMoved.splice(afterIndex + 1, 0, move.chunkId);
  return withoutMoved;
}

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
 *   {@link ActivityPanel}'s live feed.
 *
 * The READY and BACKLOG lanes are board cards like every other chunk, reordered in
 * place rather than rendered as a second surface.
 * This page owns the writes those affordances imply, since {@link BoardShell} is
 * presentational: the queue and backlog reads feed each lane's order, and the
 * lane-tagged reposition events route to `POST /api/queue/position` or
 * `POST /api/backlog/position` (`bzh:ranking-is-per-list`).
 *
 * The titlebar, the {@link FleetLiveUpdates} spine, and the TanStack `QueryClient`
 * stay at the app root — none of them move here, so navigating away from and back
 * to `/board` never restarts the SSE stream or drops the query cache.
 *
 * Which card is open is **the URL's**, not this component's: `?chunk=…` on
 * `/board`, read and written through the shared {@link injectChunkUrlSelection}
 * — the same contract the runner's local panel uses. Both selection
 * sources — a board card and an ask in the right rail — write the same param,
 * so a board is shareable, a reload keeps its place, and back/forward walk the
 * selection history.
 */
@Component({
  selector: 'app-board-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardShell, ChunkDetail, ActivityPanel, QuestionsPanel, RunnerPanel],
  templateUrl: './board-page.html',
  styleUrl: './board-page.css',
})
export class BoardPage {
  private readonly chunksQuery = injectHubChunksQuery();
  private readonly queueQuery = injectHubQueueQuery();
  private readonly repositionQueue = injectRepositionQueueMutation();
  private readonly repositionBacklog = injectRepositionBacklogMutation();
  private readonly selection = injectChunkUrlSelection();
  private readonly meQuery = injectMeQuery();

  /** Whether the current identity may promote a backlog chunk (`chunk:control` —
   * issue #210). Withholds the board card's Promote control when `false`; `null`/pending
   * resolves to `false` (hidden until confirmed), the same convention `RunnerPanel`'s
   * `canPause` set. */
  protected readonly canControl = computed(() => hasPermission(this.meQuery.data(), 'chunk:control'));

  /** Whether the current identity may reorder the ready queue or backlog
   * (`queue:reorder` — issue #210). Withholds their drag-and-drop when `false`
   * — a read-only board must not *arm* a drag it would then refuse.
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

  /** Every chunk id the shared `promoteChunk` mutation is currently in flight for —
   * one mutation instance fires once per promoted card, so this is scoped by
   * `mutationKey` + variables rather than the mutation's bare `isPending()`, which
   * would read `true` for every not-ready card while any one of them is promoting. */
  private readonly pendingPromotes = injectPendingMutationVariables<PromoteVars>(promoteChunkMutationKey);

  /** Every reposition the shared `repositionQueue`/`repositionBacklog` mutations are
   * currently in flight for, scoped by `mutationKey` the same way {@link pendingPromotes}
   * is — {@link readyLaneOrder}/{@link backlogLaneOrder} fold these into the requested
   * order while the round trip is outstanding. */
  private readonly pendingRepositionQueue = injectPendingMutationVariables<RepositionVars>(repositionQueueMutationKey);
  private readonly pendingRepositionBacklog = injectPendingMutationVariables<RepositionVars>(
    repositionBacklogMutationKey,
  );

  /** Every chunk id a delete mutation is currently in flight for — read by
   * {@link boardChunks} to hide the row while it settles (`bzh:frontend-pending-override`'s
   * "Chunk detail Delete → chunk is hidden from lists" row). The delete mutation itself is
   * owned and fired by `ChunkDetail`'s dock, not this container; `mutationKey`-scoped reads
   * are exactly what let a list surface see another component's in-flight mutation without
   * owning it. */
  private readonly pendingDeletes = injectPendingMutationVariables<DeleteVars>(chunkDeleteMutationKey);

  /** The board's last operator-action failure — a promote or a reorder — or `null`.
   * Reset at the start of every new attempt (issue #42's "report, don't swallow",
   * the same convention `ChunkDetail`'s own `actionError` follows). */
  protected readonly actionError = signal<string | null>(null);

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

  /**
   * {@link chunks}, with each pending-promote chunk's status overridden to `'ready'`
   * (`bzh:frontend-pending-override`) — this is what actually moves its card into the READY
   * lane while `promoteChunk` is in flight, since {@link BoardShell} groups every card by
   * its `status` field alone. The override is total and always safe here: a chunk resting
   * at `not_ready` has no other status that could simultaneously outrank a promote
   * (`blizzard-context:/domain/work/statuses.md`), so there is no "not predictable, fall
   * back to disabled" case to guard for. Fed to `BoardShell` in place of {@link chunks}
   * itself; {@link boardState} and {@link selected} stay off the real list, since neither
   * cares about a card's rendered status. Purely computed off `pendingPromotes`' own
   * variables — nothing here touches the query cache, so a rejected promote reverts to
   * `not_ready` for free the instant `isPending()` flips false.
   *
   * Also drops any chunk with a pending delete — {@link pendingDeletes} — entirely, rather
   * than overriding a field: a chunk mid-delete has no predictable *status* to render (the
   * override is about ceasing to exist, not becoming some other status), and the item's own
   * table asks for it "hidden from lists" outright. A rejected delete reverts it to visible
   * for free the same way the status override reverts, once `isPending()` flips false.
   */
  protected readonly boardChunks = computed<readonly ChunkSummary[]>(() => {
    const pendingPromoted = this.pendingPromotes();
    const deletingIds = new Set(this.pendingDeletes().map((vars) => vars.chunkId));
    const visible = deletingIds.size === 0 ? this.chunks() : this.chunks().filter((c) => !deletingIds.has(c.chunk_id));
    if (pendingPromoted.length === 0) return visible;
    const pendingIds = new Set(pendingPromoted.map((vars) => vars.chunkId));
    return visible.map((chunk) => (pendingIds.has(chunk.chunk_id) ? { ...chunk, status: 'ready' } : chunk));
  });

  /**
   * {@link readyOrder}, with a pending reposition targeting the ready queue folded
   * in — fed to `BoardShell` in {@link readyOrder}'s place. A pending promote's chunk
   * id is deliberately left out of this order rather than injected at some predicted
   * rank: the hub always assigns a freshly-promoted chunk a **tail** position
   * (`promote.py::tail_position` — it appends after every currently-ready chunk, never
   * the head), so the card's real landing spot is the *bottom* of READY, which is
   * exactly what `BoardShell.cards()`'s own "unranked id" fallback (`rankOf`, which
   * sorts an id absent from `readyOrder` to the bottom of the lane) already yields for
   * free — {@link boardChunks}' status override alone is what moves the card into the
   * READY lane, and its id is simply never added to this order to rank there.
   *
   * Purely computed off the reposition mutation's own variables; a rejected reposition
   * reverts to the real `readyOrder` for free the instant its `isPending()` flips false.
   */
  protected readonly readyLaneOrder = computed<readonly string[]>(() => {
    let order = this.readyOrder();
    for (const move of this.pendingRepositionQueue()) {
      order = withRequestedPosition(order, move);
    }
    return order;
  });

  /** {@link backlogOrder}, with a pending backlog reposition's requested placement folded
   * in — the BACKLOG-lane counterpart of {@link readyLaneOrder}'s reposition half. No
   * promote override belongs here: a pending promote leaves the backlog lane entirely via
   * {@link boardChunks}' status override, so its old backlog rank is simply never consulted. */
  protected readonly backlogLaneOrder = computed<readonly string[]>(() => {
    let order = this.backlogOrder();
    for (const move of this.pendingRepositionBacklog()) {
      order = withRequestedPosition(order, move);
    }
    return order;
  });

  /** A READY or BACKLOG card dropped somewhere new — placed after the anchor it
   * landed on (`null` = the very top), routed to the matching list's mutation. */
  protected reposition(move: BoardReposition): void {
    this.actionError.set(null);
    const mutation = move.list === 'notready' ? this.repositionBacklog : this.repositionQueue;
    mutation.mutate(
      { chunkId: move.chunkId, afterChunkId: move.afterChunkId },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Reorder failed.')) },
    );
  }

  /** Promote a backlog chunk — the board card's own Promote button. */
  protected onPromote(chunkId: string): void {
    this.actionError.set(null);
    this.promoteChunk.mutate(
      { chunkId },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Promote failed.')) },
    );
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

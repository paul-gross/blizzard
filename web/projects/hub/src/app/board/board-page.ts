import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import { BoardShell } from './board-shell/board-shell';
import { type BoardReposition } from './board-shell/board-column';
import { type ChunkSummary, asyncState, errorMessage, injectChunkUrlSelection, type KitAsyncStateValue, injectHubChunkDetailQuery, injectPendingMutationVariables } from 'fleet';
import { ChunkDetail } from './chunk-dock/chunk-detail';
import { ActivityPanel } from './activity/activity-panel';
import { QuestionsPanel } from './questions/questions-panel';
import { GatesPanel } from './gates/gates-panel';
import { injectHubDecisionsQuery } from './gates/gates.query';
import { RunnerPanel } from '../fleet/runner-panel';
import { chunkDeleteMutationKey, promoteChunkMutationKey, repositionBacklogMutationKey, repositionQueueMutationKey } from '../mutation-keys';
import { hasPermission, injectMeQuery } from '../auth/me.query';
import { type DeleteVars } from './chunks/delete.mutations';
import { injectHubBacklogQuery, injectHubQueueQuery } from './queue/queue.query';
import { injectHubBoardChunksQuery } from './chunks/chunks.query';
import { injectHubChunkCountsQuery } from './chunks/chunk-counts.query';
import { injectPromoteChunkMutation, type PromoteVars } from './chunks/promote.mutations';
import { injectRepositionBacklogMutation, injectRepositionQueueMutation, type RepositionVars } from './queue/queue.mutations';

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
 *   {@link GatesPanel}, the fleet's open gates, opened the same way — then
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
  imports: [BoardShell, ChunkDetail, ActivityPanel, QuestionsPanel, GatesPanel, RunnerPanel],
  templateUrl: './board-page.html',
  styleUrl: './board-page.css',
})
export class BoardPage {
  /** The board's last operator-action failure — a promote or a reorder — or `null`.
   * Reset at the start of every new attempt (the "report, don't swallow" convention,
   * the same one `ChunkDetail`'s own `actionError` follows). */
  protected readonly actionError = signal<string | null>(null);

  private readonly chunksQuery = injectHubBoardChunksQuery();
  private readonly countsQuery = injectHubChunkCountsQuery();
  private readonly queueQuery = injectHubQueueQuery();
  /** The fleet-wide open-decision read — the same cache entry {@link GatesPanel} reads. */
  private readonly decisionsQuery = injectHubDecisionsQuery();
  private readonly repositionQueue = injectRepositionQueueMutation((error) =>
    this.actionError.set(errorMessage(error, 'Reorder failed.')),
  );
  private readonly repositionBacklog = injectRepositionBacklogMutation((error) =>
    this.actionError.set(errorMessage(error, 'Reorder failed.')),
  );
  private readonly selection = injectChunkUrlSelection();
  private readonly meQuery = injectMeQuery();

  /** Whether the current identity may promote a backlog chunk (`chunk:control`).
   * Withholds the board card's Promote control when `false`; `null`/pending
   * resolves to `false` (hidden until confirmed), the same convention `RunnerPanel`'s
   * `canPause` set. */
  protected readonly canControl = computed(() => hasPermission(this.meQuery.data(), 'chunk:control'));

  /** Whether the current identity may reorder the ready queue or backlog
   * (`queue:reorder`). Withholds their drag-and-drop when `false`
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

  /** The linked chunk's own detail read — the dock's same cache entry, so it costs no
   * extra request; it admits a linked chunk the windowed list does not carry. */
  private readonly linkedDetail = injectHubChunkDetailQuery(() => this.selection.chunkId());

  /** The all-time fleet counts the column heads show; `null` until the read resolves. */
  protected readonly counts = computed(() => this.countsQuery.data() ?? null);

  /** The board's chunk list — `done` chunks older than the board window are not in it;
   * empty until the first read resolves. */
  protected readonly chunks = computed(() => this.chunksQuery.data() ?? []);

  /** Every chunk id with an open decision — joined here, in the container, so each
   * card takes a plain `gate` flag rather than reading the decisions itself. */
  protected readonly gatedChunkIds = computed<ReadonlySet<string>>(
    () => new Set((this.decisionsQuery.data() ?? []).map((decision) => decision.chunk_id)),
  );

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
   * (`bzh:frontend-pending-override`, total per `blizzard-context:/domain/work/statuses.md`)
   * and each pending-delete chunk dropped — pinned by `board-page.spec.ts`'s "moves only the
   * clicked card into READY while its mutation is pending, restoring it to BACKLOG once
   * settled" and "drops the row while its delete is pending, and restores it once the delete
   * settles". Fed to `BoardShell` in place of {@link chunks}; {@link boardState} and
   * {@link selected} stay off the real list.
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
   * (it appends after every currently-ready chunk, never the head), so the card's real landing spot is the *bottom* of READY, which is
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
    mutation.mutate({ chunkId: move.chunkId, afterChunkId: move.afterChunkId });
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
   * read from the URL, never from local state.
   *
   * Held to a chunk that exists: one in the board list, or one whose own detail read
   * resolved — the board list omits old `done` chunks, which still open by link. A
   * `chunk` param naming a chunk that no longer exists (or one that has not
   * arrived yet, on the first frame before either read resolves) reads as
   * no-selection, so the dock shows its normal rest state instead of chasing a
   * detail that will 404. The param itself is left alone — the board never
   * rewrites the URL to "correct" it, so a link that is merely early still
   * opens its chunk the moment a read lands.
   */
  protected readonly selected = computed<string | null>(() => {
    const chunkId = this.selection.chunkId();
    if (chunkId === null) return null;
    if (this.chunks().some((chunk) => chunk.chunk_id === chunkId)) return chunkId;
    return this.linkedDetail.data()?.chunk_id === chunkId ? chunkId : null;
  });

  /** Open a chunk in the dock — or clear it — by writing the URL. */
  protected select(chunkId: string | null): void {
    this.selection.select(chunkId);
  }
}

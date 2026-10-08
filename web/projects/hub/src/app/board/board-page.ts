import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import { BoardShell } from './board-shell/board-shell';
import { type BoardReposition } from './board-shell/board-column';
import { type ChunkSummary, asyncState, errorMessage, injectChunkUrlSelection, type KitAsyncStateValue, injectHubChunkDetailQuery, injectPendingMutationVariables } from 'fleet';
import { ChunkDetail } from './chunk-dock/chunk-detail';
import { ActivityPanel } from './activity/activity-panel';
import { QuestionsPanel } from './questions/questions-panel';
import { GatesPanel } from './gates/gates-panel';
import { injectHubDecisionsQuery } from './gates/gates.query';
import { RunnerPanel } from '../runners/runner-panel';
import { chunkDeleteMutationKey, promoteChunkMutationKey, repositionBacklogMutationKey, repositionQueueMutationKey } from '../core/mutation-keys';
import { hasPermission, injectMeQuery } from '../core/auth/me.query';
import { type DeleteVars } from './chunks/delete.mutations';
import { injectHubBacklogQuery, injectHubQueueQuery } from './queue/queue.query';
import { injectHubBoardChunksQuery } from '../core/chunks.query';
import { injectHubChunkCountsQuery } from './chunks/chunk-counts.query';
import { injectPromoteChunkMutation, type PromoteVars } from './chunks/promote.mutations';
import { injectRepositionBacklogMutation, injectRepositionQueueMutation, type RepositionVars } from './queue/queue.mutations';
import { foldRepositions, gatedChunkIds, orderedChunkIds, resolveSelectedChunk, withPendingBoardChanges } from './board-page.model';

/**
 * The board route — the two-column mission-control surface:
 *
 * - the **centre** stacks {@link BoardShell} over the always-mounted {@link ChunkDetail}
 *   dock, so selecting or deselecting a card never reflows the board;
 * - the **right rail** holds {@link RunnerPanel}, {@link QuestionsPanel},
 *   {@link GatesPanel}, and {@link ActivityPanel}.
 *
 * This page owns the board's writes, since {@link BoardShell} is presentational: the
 * queue and backlog reads feed each lane's order, and lane-tagged reposition events
 * route to the matching list's mutation (`bzh:ranking-is-per-list`).
 *
 * Which card is open is the URL's `?chunk=…`, read and written through
 * {@link injectChunkUrlSelection}.
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
   * Reset at the start of every new attempt. */
  protected readonly actionError = signal<string | null>(null);

  private readonly chunksQuery = injectHubBoardChunksQuery();
  private readonly countsQuery = injectHubChunkCountsQuery();
  private readonly queueQuery = injectHubQueueQuery();
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
   * Pending resolves to `false`, so the control stays hidden until confirmed. */
  protected readonly canControl = computed(() => hasPermission(this.meQuery.data(), 'chunk:control'));

  /** Whether the current identity may reorder the ready queue or backlog
   * (`queue:reorder`). Declared before {@link backlogQuery}, whose `enabled` gate
   * reads it during field initialization. */
  protected readonly canReorder = computed(() => hasPermission(this.meQuery.data(), 'queue:reorder'));

  /** The backlog's hub-ordered read — the BACKLOG lane's ranking. Gated on
   * {@link canReorder}, so a board without `queue:reorder` never attempts it. */
  private readonly backlogQuery = injectHubBacklogQuery(this.canReorder);

  /** Promote a backlog chunk to ready from its board card. */
  protected readonly promoteChunk = injectPromoteChunkMutation();

  /** Every chunk id the shared `promoteChunk` mutation is currently in flight for — scoped
   * by variables, since its bare `isPending()` is true while any card is promoting. */
  private readonly pendingPromotes = injectPendingMutationVariables<PromoteVars>(promoteChunkMutationKey);

  /** Every reposition the shared `repositionQueue`/`repositionBacklog` mutations are
   * currently in flight for. */
  private readonly pendingRepositionQueue = injectPendingMutationVariables<RepositionVars>(repositionQueueMutationKey);
  private readonly pendingRepositionBacklog = injectPendingMutationVariables<RepositionVars>(
    repositionBacklogMutationKey,
  );

  /** Every chunk id a delete mutation is currently in flight for, read by `mutationKey`
   * since this container does not own the mutation (`bzh:frontend-pending-override`). */
  private readonly pendingDeletes = injectPendingMutationVariables<DeleteVars>(chunkDeleteMutationKey);

  /** The linked chunk's own detail read — it admits a linked chunk the windowed list does not carry. */
  private readonly linkedDetail = injectHubChunkDetailQuery(() => this.selection.chunkId());

  /** The all-time fleet counts the column heads show; `null` until the read resolves. */
  protected readonly counts = computed(() => this.countsQuery.data() ?? null);

  /** The board's chunk list — `done` chunks older than the board window are not in it;
   * empty until the first read resolves. */
  protected readonly chunks = computed(() => this.chunksQuery.data() ?? []);

  /** Every chunk id with an open decision. */
  protected readonly gatedChunkIds = computed<ReadonlySet<string>>(
    () => gatedChunkIds(this.decisionsQuery.data() ?? []),
  );

  /** The board's async state — derived from the chunks query
   * alone: the queue read only supplies the READY lane's order, so it never
   * gates the board's emptiness. */
  protected readonly boardState = computed<KitAsyncStateValue>(() =>
    asyncState(this.chunksQuery, this.chunks().length === 0),
  );

  /** The ready queue in the hub's dispatch order, as bare ids — the READY lane's ordering. */
  protected readonly readyOrder = computed<readonly string[]>(() =>
    orderedChunkIds(this.queueQuery.data() ?? []),
  );

  /** The backlog in the hub's order, as bare ids — the BACKLOG lane's ordering. */
  protected readonly backlogOrder = computed<readonly string[]>(() =>
    orderedChunkIds(this.backlogQuery.data() ?? []),
  );

  /** {@link chunks} with pending board changes applied (`withPendingBoardChanges`);
   * {@link boardState} and {@link selected} stay off the real list. */
  protected readonly boardChunks = computed<readonly ChunkSummary[]>(() =>
    withPendingBoardChanges(this.chunks(), this.pendingPromotes(), this.pendingDeletes()),
  );

  /** {@link readyOrder} with pending ready-queue repositions folded in (`foldRepositions`). */
  protected readonly readyLaneOrder = computed<readonly string[]>(() =>
    foldRepositions(this.readyOrder(), this.pendingRepositionQueue()),
  );

  /** {@link backlogOrder} with pending backlog repositions folded in (`foldRepositions`). */
  protected readonly backlogLaneOrder = computed<readonly string[]>(() =>
    foldRepositions(this.backlogOrder(), this.pendingRepositionBacklog()),
  );

  /** A READY or BACKLOG card dropped somewhere new — placed after the anchor it
   * landed on (`null` = the very top), routed to the matching list's mutation. */
  protected reposition(move: BoardReposition): void {
    this.actionError.set(null);
    const mutation = move.list === 'notready' ? this.repositionBacklog : this.repositionQueue;
    mutation.mutate({ chunkId: move.chunkId, afterChunkId: move.afterChunkId });
  }

  /** Promote a backlog chunk. */
  protected onPromote(chunkId: string): void {
    this.actionError.set(null);
    this.promoteChunk.mutate(
      { chunkId },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Promote failed.')) },
    );
  }

  /** The selected chunk (`resolveSelectedChunk`). The URL param itself is never
   * rewritten, so a link that is merely early still opens its chunk once a read lands. */
  protected readonly selected = computed<string | null>(() =>
    resolveSelectedChunk(this.selection.chunkId(), this.chunks(), this.linkedDetail.data()?.chunk_id),
  );

  /** Select a chunk — or clear the selection — by writing the URL. */
  protected select(chunkId: string | null): void {
    this.selection.select(chunkId);
  }
}

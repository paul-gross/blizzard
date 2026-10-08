import { ChangeDetectionStrategy, Component, computed, effect, inject, input, output, signal } from '@angular/core';

import { type ChunkDetail as ChunkDetailAggregate, type ChunkStatus, injectHubChunkDetailQuery, injectHubChunkWorkItemsQuery, errorMessage, FLEET_CLOCK, KitAsyncState, type KitAsyncStateValue, injectPendingMutationVariables, isPendingFor, asyncState, type WorkItemsState, type AnswerQuestionEvent, type EditGraphEvent, type ResolveDecisionEvent } from 'fleet';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectCompleteChunkMutation, type CompleteVars } from '../chunks/complete.mutations';
import { type DeleteVars, injectDeleteChunkMutation } from '../chunks/delete.mutations';
import { type DetachVars, injectDetachChunkMutation } from '../chunks/detach.mutations';
import { type ChunkGraphEditVars, injectSetChunkGraphMutation } from '../chunks/edit.mutations';
import {
  type AnswerFailure,
  type AnswerVars,
  type ResolveVars,
  injectAnswerQuestionMutation,
  injectResolveDecisionMutation,
  readAnswerFailure,
  readDecisionFailure,
} from '../chunks/human.mutations';
import { injectChunkPauseMutation, type ChunkPauseVars } from '../chunks/pause.mutations';
import {
  answerQuestionMutationKey,
  chunkCompleteMutationKey,
  chunkDeleteMutationKey,
  chunkDetachMutationKey,
  chunkPauseMutationKey,
  chunkSetGraphMutationKey,
  resolveDecisionMutationKey,
} from '../../core/mutation-keys';
import { openDetail, openWorkItems, pendingQuestionIds, pendingStatusOverride } from './chunk-detail.model';
import { ChunkDetailPanel } from './chunk-detail-panel';

/**
 * The chunk detail **container** (`bzh:frontend-container-presentational`) — owns the
 * detail query and the operator-action mutations, and renders {@link ChunkDetailPanel}
 * over them, or a rest state while no chunk is open.
 *
 * Every action reports through two channels: `actionError` for a failure, and
 * `actionOutcome` for a non-failure result that still needs saying (a lost answer race,
 * naming the winner). Both clear on the next attempt and whenever a different chunk opens.
 */
@Component({
  selector: 'app-chunk-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkDetailPanel, KitAsyncState],
  templateUrl: './chunk-detail.html',
  styleUrl: './chunk-detail.css',
})
export class ChunkDetail {
  /** The selected chunk id, or `null` when the dock is closed. */
  readonly chunkId = input<string | null>(null);

  /** The graphs view's path segments, forwarded to {@link ChunkDetailPanel.graphLinkBase};
   * `null` withholds the graph links. */
  readonly graphLinkBase = input<readonly string[] | null>(null);

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  private readonly detailQuery = injectHubChunkDetailQuery(() => this.chunkId());
  private readonly workItemsQuery = injectHubChunkWorkItemsQuery(() => this.chunkId());
  private readonly answerMutation = injectAnswerQuestionMutation();
  private readonly pendingAnswers = injectPendingMutationVariables<AnswerVars>(answerQuestionMutationKey);
  private readonly resolveMutation = injectResolveDecisionMutation();
  private readonly detachMutation = injectDetachChunkMutation();
  private readonly pauseMutation = injectChunkPauseMutation();
  private readonly completeMutation = injectCompleteChunkMutation();
  private readonly deleteMutation = injectDeleteChunkMutation();
  private readonly editGraphMutation = injectSetChunkGraphMutation();
  private readonly meQuery = injectMeQuery();
  private readonly clock = inject(FLEET_CLOCK);

  /** Whether the current identity may pause/resume/detach or set the chunk's graph
   * (`chunk:control`); `null`/pending resolves to `false`. */
  protected readonly canControl = computed(() => hasPermission(this.meQuery.data(), 'chunk:control'));

  /** Whether the current identity may answer an open question (`question:answer`). */
  protected readonly canAnswer = computed(() => hasPermission(this.meQuery.data(), 'question:answer'));

  /** Whether the current identity may resolve an open gate decision (`gate:resolve`). */
  protected readonly canResolve = computed(() => hasPermission(this.meQuery.data(), 'gate:resolve'));

  /** Every chunk id a Detach mutation is currently pending for, with its variables
   * (`bzh:frontend-pending-override`). */
  private readonly pendingChunkDetaches = injectPendingMutationVariables<DetachVars>(chunkDetachMutationKey);

  /** The same, for Delete. */
  private readonly pendingChunkDeletes = injectPendingMutationVariables<DeleteVars>(chunkDeleteMutationKey);

  /** The same, for Resolve decision. */
  private readonly pendingResolves = injectPendingMutationVariables<ResolveVars>(resolveDecisionMutationKey);

  /** The same, for Set graph. */
  private readonly pendingGraphEdits = injectPendingMutationVariables<ChunkGraphEditVars>(chunkSetGraphMutationKey);

  /** Whether a Pause/Resume is in flight for the open chunk. Scoped to `chunkId` because
   * this container stays mounted across a selection change: a pause fired on one chunk must not disable another's controls,
   * and must still disable its own when that chunk is re-selected. */
  protected readonly pausePending = computed(() =>
    isPendingFor(this.pendingChunkPauses(), (v) => v.chunkId === this.chunkId()),
  );

  /** Whether a Detach is in flight for the open chunk. */
  protected readonly detachPending = computed(() =>
    isPendingFor(this.pendingChunkDetaches(), (v) => v.chunkId === this.chunkId()),
  );

  /** Whether a Complete is in flight for the open chunk. */
  protected readonly completePending = computed(() =>
    isPendingFor(this.pendingChunkCompletes(), (v) => v.chunkId === this.chunkId()),
  );

  /** Whether a Delete is in flight for the open chunk. */
  protected readonly deletePending = computed(() =>
    isPendingFor(this.pendingChunkDeletes(), (v) => v.chunkId === this.chunkId()),
  );

  /** Whether a Set graph is in flight for the open chunk. */
  protected readonly graphPending = computed(() =>
    isPendingFor(this.pendingGraphEdits(), (v) => v.chunkId === this.chunkId()),
  );

  /** Every chunk id a Pause/Resume mutation is currently pending for, with its variables
   * (`bzh:frontend-pending-override`). */
  private readonly pendingChunkPauses = injectPendingMutationVariables<ChunkPauseVars>(chunkPauseMutationKey);

  /** The same, for Complete. */
  private readonly pendingChunkCompletes = injectPendingMutationVariables<CompleteVars>(chunkCompleteMutationKey);

  /** The pending Pause or Complete status override, or `null` — {@link pendingStatusOverride}. */
  protected readonly overrideStatus = computed<ChunkStatus | null>(() =>
    pendingStatusOverride(this.detail(), this.pendingChunkCompletes(), this.pendingChunkPauses()),
  );

  /** The chunk's rendered status — {@link overrideStatus} while it names one, else the
   * real `detail.status` (`bzh:frontend-pending-override`). */
  protected renderedStatus(detail: ChunkDetailAggregate): ChunkStatus {
    return this.overrideStatus() ?? detail.status;
  }

  /** Whether a resolve-decision is in flight for the open chunk. */
  protected readonly resolvePending = computed(() =>
    isPendingFor(this.pendingResolves(), (v) => v.chunkId === this.chunkId()),
  );

  /** The ids of the questions an answer mutation is in flight for. */
  protected readonly pendingAnswerQuestionIds = computed(() => pendingQuestionIds(this.pendingAnswers()));

  /** The open chunk's last operator-action failure, or `null`. */
  protected readonly actionError = signal<string | null>(null);

  /** The open chunk's last operator-action **outcome** — a non-failure result that still
   * needs saying: a lost answer race, carrying the winning answer. Pinned by
   * `chunk-detail.spec.ts`'s "renders the winner’s name and answer as an outcome when the
   * answer race is lost". */
  protected readonly actionOutcome = signal<string | null>(null);

  constructor() {
    effect(() => {
      this.chunkId();
      this.beginAction();
    });
  }

  /** Clear both report channels together — every action starts here, and so does
   * opening a different chunk. */
  private beginAction(): void {
    this.actionError.set(null);
    this.actionOutcome.set(null);
  }

  /** The open chunk's aggregate, or `undefined` while closed / still loading. */
  protected readonly detail = computed(() => openDetail(this.chunkId(), this.detailQuery.data()));

  /**
   * The detail read's async state. Read only once a chunk is open: the query is
   * disabled while `chunkId()` is `null`, and a disabled query reports `isPending()`
   * forever. Never `'empty'`: a single aggregate either resolves or errors.
   */
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.detailQuery, false));

  /** The open chunk's related work items + fetch state for the Issue tab. A failed
   * read (unreachable hub / no work-source) becomes `error` so the tab shows a visible notice. */
  protected readonly workItems = computed<WorkItemsState>(() => openWorkItems(this.chunkId(), this.workItemsQuery));

  /** Answer an open question; a lost race reports as an outcome ({@link readAnswerFailure}). */
  protected onAnswer(event: AnswerQuestionEvent): void {
    this.beginAction();
    this.answerMutation.mutate(
      { questionId: event.questionId, answer: event.answer, chunkId: event.chunkId },
      { onError: (error) => this.reportAnswerFailure(error) },
    );
  }

  /** Route an answer failure to the outcome or error channel — the fold is
   * {@link readAnswerFailure}'s. */
  private reportAnswerFailure(error: unknown): void {
    this.reportFailure(readAnswerFailure(error));
  }

  /** Route a folded failure to the outcome or error channel. */
  private reportFailure(failure: AnswerFailure): void {
    if (failure.kind === 'outcome') this.actionOutcome.set(failure.message);
    else this.actionError.set(failure.message);
  }

  protected onResolve(event: ResolveDecisionEvent): void {
    this.beginAction();
    this.resolveMutation.mutate(
      {
        decisionId: event.decisionId,
        choice: event.choice,
        chunkId: event.chunkId,
      },
      { onError: (error) => this.reportFailure(readDecisionFailure(error, new Date(this.clock()))) },
    );
  }

  protected onDetach(chunkId: string): void {
    this.beginAction();
    this.detachMutation.mutate(
      { chunkId },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Detach failed.')) },
    );
  }

  protected onPause(chunkId: string): void {
    this.beginAction();
    this.pauseMutation.mutate(
      { chunkId, paused: true },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Pause failed.')) },
    );
  }

  protected onResume(chunkId: string): void {
    this.beginAction();
    this.pauseMutation.mutate(
      { chunkId, paused: false },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Resume failed.')) },
    );
  }

  protected onComplete(chunkId: string): void {
    this.beginAction();
    this.completeMutation.mutate(
      { chunkId },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Complete failed.')) },
    );
  }

  /** Delete an unacquired chunk; there is no undo. Success dismisses the dock — the order
   * is pinned by `chunk-detail.spec.ts`'s "dismisses the dock on a successful delete" case. */
  protected onDelete(chunkId: string): void {
    this.beginAction();
    this.deleteMutation.mutate(
      { chunkId },
      {
        onSuccess: () => this.dismiss.emit(),
        onError: (error) => this.actionError.set(errorMessage(error, 'Delete failed.')),
      },
    );
  }

  protected onEditGraph(event: EditGraphEvent): void {
    this.beginAction();
    this.editGraphMutation.mutate(
      { chunkId: event.chunkId, graphId: event.graphId },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Set graph failed.')) },
    );
  }
}

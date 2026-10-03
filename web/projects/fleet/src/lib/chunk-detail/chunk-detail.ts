import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal } from '@angular/core';

import type { ChunkDetail as ChunkDetailAggregate, ChunkStatus } from '../api/hub';
import { hasPermission, injectMeQuery } from '../auth/me.query';
import { injectHubChunkDetailQuery, injectHubChunkWorkItemsQuery } from '../chunk-page/chunk-detail.query';
import { injectCompleteChunkMutation, type CompleteVars } from '../chunks/complete.mutations';
import { injectDeleteChunkMutation } from '../chunks/delete.mutations';
import { injectDetachChunkMutation } from '../chunks/detach.mutations';
import { injectSetChunkGraphMutation } from '../chunks/edit.mutations';
import {
  type AnswerVars,
  injectAnswerQuestionMutation,
  injectResolveDecisionMutation,
  readAnswerFailure,
} from '../chunks/human.mutations';
import { injectChunkPauseMutation, type ChunkPauseVars } from '../chunks/pause.mutations';
import { errorMessage } from '../error-message';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { answerQuestionMutationKey, chunkCompleteMutationKey, chunkPauseMutationKey } from '../mutation-keys';
import { injectPendingMutationVariables, isPendingFor } from '../mutation-pending';
import { asyncState } from '../query-state';
import { deriveWorkItemsState, type WorkItemsState } from './work-items-state';
import {
  type AnswerQuestionEvent,
  ChunkDetailPanel,
  type EditGraphEvent,
  type ResolveDecisionEvent,
} from './chunk-detail-panel';

/** Whether a pending Pause's predicted `paused` outcome is total over each status —
 * exhaustive so a status added to the wire forces a decision here rather than falling
 * through an inline inequality (mirrors `chunk-lanes.ts`'s `STATUS_LANE` idiom).
 * `waiting_on_human`/`needs_human` outrank the pause fact in the hub's precedence
 * ladder (`blizzard-context:/domain/work/statuses.md`), so pausing from either leaves
 * the rendered status unchanged; every other status is safely predicted `paused`. */
const PAUSE_OVERRIDE_TOTAL: Record<ChunkStatus, boolean> = {
  not_ready: true,
  ready: true,
  running: true,
  delivering: true,
  waiting_on_human: false,
  needs_human: false,
  paused: true,
  stopped: true,
  done: true,
};

/**
 * The chunk detail **container** — owns the reactive detail query and the
 * human-loop mutations (answer a question, resolve a gate decision),
 * and renders the presentational {@link ChunkDetailPanel} over them. It stays
 * mounted in the bottom dock and shows a rest state until a card is selected; the
 * panel stays presentational and every server call goes through the generated
 * client (bzh:generated-client).
 *
 * Reactive over the selected `chunkId`: the query re-keys and disables itself while
 * nothing is open, so no request fires for the empty board. Answering, resolving,
 * detaching, pausing/resuming, completing, or editing the graph/model invalidates the
 * chunk and the fleet list, and the SSE stream corroborates. Every operator action's
 * 404/409 (422 for a blank model) is read off its mutation's `onError` and held in the
 * shared `actionError` for the panel to show — the "report, don't swallow"
 * requirement, which pause/resume, graph/model edits, and complete all follow rather
 * than reinvent — and clears on the next attempt or the
 * moment a different chunk opens. Answering has a **second** channel alongside it,
 * `actionOutcome`: a lost first-write-wins race is not a failure to retry
 * but news — someone else's answer landed — so it reads as an outcome naming the winner.
 * Both clear together in `beginAction`.
 *
 * **Delete** breaks that shape: it makes the chunk cease to exist, so
 * `onDelete` doesn't just fold a failure into `actionError` — on success it emits
 * `dismiss` too, the same event the header's close button fires. The board binds
 * `dismiss` to clearing its own selection, so the dock closes instead of sitting on a
 * chunk id its own detail query would otherwise re-read into a 404.
 */
@Component({
  selector: 'fleet-chunk-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkDetailPanel, KitAsyncState],
  templateUrl: './chunk-detail.html',
  styleUrl: './chunk-detail.css',
})
export class ChunkDetail {
  /** The selected chunk id, or `null` when the dock is closed. */
  readonly chunkId = input<string | null>(null);

  /** The graphs view's own path segments, forwarded straight to
   * {@link ChunkDetailPanel.graphLinkBase} — `null` (the default) withholds the
   * dock's graph links, since this container is mountable from any host and must
   * not hardcode a hub-only route itself. */
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

  /** Whether the current identity may pause/resume/detach or set the chunk's graph
   * (`chunk:control`). Withholds those controls in the panel below so a
   * `guest` never sees a write it cannot make; `null`/pending resolves to `false`
   * (hidden until confirmed), the same convention `RunnerPanel`'s `canPause` set. */
  protected readonly canControl = computed(() => hasPermission(this.meQuery.data(), 'chunk:control'));

  /** Whether the current identity may answer an open question (`question:answer`). */
  protected readonly canAnswer = computed(() => hasPermission(this.meQuery.data(), 'question:answer'));

  /** Whether the current identity may resolve an open gate decision (`gate:resolve`). */
  protected readonly canResolve = computed(() => hasPermission(this.meQuery.data(), 'gate:resolve'));

  /** Whether the pause/resume mutation is in flight for this chunk — read straight off
   * the mutation's own `.isPending()` and threaded to the header's Pause/Resume button,
   * so a double click cannot fire the request twice while the first still settles. This
   * dock shows exactly one chunk at a time, so there is no sibling row to distinguish
   * pending mutations by variables — unlike the board's per-card filtering, a plain
   * `.isPending()` read is the whole answer here. */
  protected readonly pausePending = computed(() => this.pauseMutation.isPending());

  /** Whether the detach mutation is in flight for this chunk, threaded to the header's
   * Detach menu item. */
  protected readonly detachPending = computed(() => this.detachMutation.isPending());

  /** Whether the complete mutation is in flight for this chunk, threaded to the header's
   * Complete menu item (combined there with {@link ChunkDetailHeader.completable}). */
  protected readonly completePending = computed(() => this.completeMutation.isPending());

  /** Whether the delete mutation is in flight for this chunk, threaded to the header's
   * Delete menu item (combined there with {@link ChunkDetailHeader.deleteDisabled}). */
  protected readonly deletePending = computed(() => this.deleteMutation.isPending());

  /** Every chunk id a Pause/Resume mutation is currently pending for, and its own
   * variables — read through the shared helper (`bzh:frontend-pending-override`)
   * rather than this component's own `pauseMutation.isPending()` alone, since the
   * override below needs the fired *direction* (`paused: true` vs. `false`), not just
   * pending-ness. */
  private readonly pendingChunkPauses = injectPendingMutationVariables<ChunkPauseVars>(chunkPauseMutationKey);

  /** The same, for Complete. */
  private readonly pendingChunkCompletes = injectPendingMutationVariables<CompleteVars>(chunkCompleteMutationKey);

  /**
   * The chunk's status as it will read once a currently pending Pause or Complete
   * settles, when that outcome is total over the rendered status
   * (`bzh:frontend-pending-override`) — `null` while nothing overrides
   * `detail().status`. Complete always predicts `done`; Pause predicts `paused` only
   * where {@link PAUSE_OVERRIDE_TOTAL} holds; Resume and Detach predict nothing.
   * Pinned by `chunk-detail.spec.ts`'s "renders the paused override while pending on a
   * chunk below the human-gated states, reverting to the real status on rejection",
   * "renders no status override while Pause is pending on a chunk already
   * waiting_on_human/needs_human — the human-gated status wins", "renders the done
   * override while Complete is pending, reverting to the real status on rejection",
   * "renders no status override while Resume is pending — the pause overlay hides what
   * status it would revert to" and "renders no status override while Detach is pending — the outcome
   * depends on facts detach never touches".
   */
  protected readonly overrideStatus = computed<ChunkStatus | null>(() => {
    const detail = this.detail();
    if (detail === undefined) return null;
    const completing = isPendingFor(this.pendingChunkCompletes(), (vars) => vars.chunkId === detail.chunk_id);
    if (completing) return 'done';
    const pausing = isPendingFor(this.pendingChunkPauses(), (vars) => vars.chunkId === detail.chunk_id && vars.paused);
    if (pausing && PAUSE_OVERRIDE_TOTAL[detail.status]) return 'paused';
    return null;
  });

  /** The chunk's status as the header's status chip renders it — {@link overrideStatus}
   * while it names one for this chunk, else the real `detail.status`
   * (`bzh:frontend-pending-override`'s container-applies-overrides rule: the header
   * receives only this already-merged result, never the raw override to reconcile
   * itself). */
  protected renderedStatus(detail: ChunkDetailAggregate): ChunkStatus {
    return this.overrideStatus() ?? detail.status;
  }

  /** Whether the resolve-decision mutation is in flight for this chunk, threaded to the
   * awaiting-human gate's choice chips. */
  protected readonly resolvePending = computed(() => this.resolveMutation.isPending());

  /** The ids of the questions an answer mutation is in flight for, threaded to the
   * awaiting-human gate's option chips and Answer buttons. */
  protected readonly pendingAnswerQuestionIds = computed(() =>
    this.pendingAnswers().map((vars) => vars.questionId),
  );

  /** The open chunk's last operator-action failure, or `null`. Reset on every new
   * attempt and whenever a different chunk opens. Shared by every action
   * in the dock — detach, pause, resume, complete. */
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

  /** Clear both report channels — every action in the dock starts here, and so does
   * opening a different chunk. One method rather than a reset per handler because the
   * two channels have to move together: leaving a stale outcome up while a *different*
   * action reports a failure renders the cyan "alice answered first" and a red notice
   * side by side, reading as though the two are about the same thing. */
  private beginAction(): void {
    this.actionError.set(null);
    this.actionOutcome.set(null);
  }

  /** The open chunk's aggregate, or `undefined` while closed / still loading. */
  protected readonly detail = computed(() => (this.chunkId() === null ? undefined : this.detailQuery.data()));

  /**
   * The detail read's async state (AC 5) — consulted only once the template's
   * own "nothing selected" branch has already ruled that case out. This
   * matters because the query is `enabled: false` while `chunkId()` is
   * `null` (`bzh:frontend-container-presentational`'s conditional-query
   * shape), and a disabled query reports `isPending()` as permanently `true`
   * — reading this triad before that branch would render the rest state as
   * an endless spinner instead. Never `'empty'`: a single chunk aggregate
   * either resolves or the read errors, the same reasoning `graph-detail.ts`
   * documents for its own single-resource read.
   */
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.detailQuery, false));

  /** The open chunk's related work items + fetch state for the Issue tab. A failed
   * read (unreachable hub / no work-source) becomes `error` so the tab shows a visible notice. */
  protected readonly workItems = computed<WorkItemsState>(() => {
    if (this.chunkId() === null) return { status: 'loading', items: [] };
    return deriveWorkItemsState(this.workItemsQuery);
  });

  /** Answer an open question. A lost first-write-wins race comes back as a 409 whose body
   * is the *winning* answer, so it is reported as an outcome naming the winner rather than
   * folded through `errorMessage()` into a generic failure; any other failure
   * stays on the error channel. Either way the mutation re-reads the chunk, so the dock
   * settles showing the question answered with its trail. */
  protected onAnswer(event: AnswerQuestionEvent): void {
    this.beginAction();
    this.answerMutation.mutate(
      { questionId: event.questionId, answer: event.answer, chunkId: event.chunkId },
      { onError: (error) => this.reportAnswerFailure(error) },
    );
  }

  /** Route an answer failure to the outcome or error channel — the fold is
   * `readAnswerFailure`'s, shared with the mobile board so both read the same. */
  private reportAnswerFailure(error: unknown): void {
    const failure = readAnswerFailure(error);
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
        struck: event.struck,
      },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Resolve failed.')) },
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

  /** Delete an unacquired chunk — withdraws its hub item(s); there is
   * no undo. Unlike every other action here, success dismisses the dock: the chunk this
   * query is keyed to no longer exists, and `deleteMutation`'s own `onSettled` already
   * invalidates the fleet list, the ready queue, the backlog, and this chunk's own detail
   * query, so leaving the dock open would have it re-read straight into a 404. Emitting
   * `dismiss` before that re-read can render clears the board's selection (`chunkId()`
   * flows to `null`), which disables the detail query for this component's next render
   * — the same `enabled: false` gate the empty-dock rest state already leans on — rather
   * than reacting to the now-orphaned response. */
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

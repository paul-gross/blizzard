import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal } from '@angular/core';

import { injectHubChunkDetailQuery } from '../chunks/chunk-detail.query';
import { injectHubChunkWorkItemsQuery } from '../chunks/chunk-work-items.query';
import { injectDetachChunkMutation } from '../chunks/detach.mutations';
import { injectSetChunkGraphMutation } from '../chunks/edit.mutations';
import {
  injectAnswerQuestionMutation,
  injectResolveDecisionMutation,
  readAnswerFailure,
} from '../chunks/human.mutations';
import { injectChunkPauseMutation } from '../chunks/pause.mutations';
import { errorMessage } from '../error-message';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { asyncState } from '../query-state';
import {
  type AnswerQuestionEvent,
  ChunkDetailPanel,
  type EditGraphEvent,
  type WorkItemsState,
  type ResolveDecisionEvent,
} from './chunk-detail-panel';

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
 * detaching, pausing/resuming, or editing the graph/model invalidates the chunk and the
 * fleet list, and the SSE stream corroborates. Every operator action's 404/409 (422 for
 * a blank model) is read off its mutation's `onError` and held in the shared
 * `actionError` for the panel to show — issue #42's "report, don't swallow"
 * requirement, which issue #46's pause/resume and issue #27's graph/model edits both
 * follow rather than reinvent — and clears on the next attempt or the moment a
 * different chunk opens. Answering has a **second** channel alongside it,
 * `actionOutcome` (issue #165): a lost first-write-wins race is not a failure to retry
 * but news — someone else's answer landed — so it reads as an outcome naming the winner.
 * Both clear together in `beginAction`.
 */
@Component({
  selector: 'fleet-chunk-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkDetailPanel, KitAsyncState],
  template: `
    @if (chunkId() === null) {
      <p class="rest" data-testid="chunk-detail-empty">SELECT A CHUNK TO SEE ITS HISTORY &amp; ARTIFACTS</p>
    } @else {
      <fleet-kit-async-state
        [state]="state()"
        loadingText="LOADING…"
        loadingTestid="chunk-detail-loading"
        errorText="FAILED TO LOAD CHUNK"
        errorTestid="chunk-detail-error"
      >
        @if (detail(); as d) {
          <fleet-chunk-detail-panel
            [detail]="d"
            [workItems]="workItems()"
            [actionError]="actionError()"
            [actionOutcome]="actionOutcome()"
            (dismiss)="dismiss.emit()"
            (answerQuestion)="onAnswer($event)"
            (resolveDecision)="onResolve($event)"
            (detach)="onDetach($event)"
            (pauseChunk)="onPause($event)"
            (resumeChunk)="onResume($event)"
            (editGraph)="onEditGraph($event)"
          />
        }
      </fleet-kit-async-state>
    }
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
    }
    /* The dock is always mounted; when no chunk is open it holds this rest state
       so the bottom dock keeps its shape (top edge, height) whether empty or filled. */
    .rest {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      margin: 0;
      border-top: 1px solid var(--bezel);
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
      color: var(--label-dim);
      font-size: var(--fs-sm);
      letter-spacing: 0.12em;
    }
  `,
})
export class ChunkDetail {
  /** The selected chunk id, or `null` when the dock is closed. */
  readonly chunkId = input<string | null>(null);

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  private readonly detailQuery = injectHubChunkDetailQuery(() => this.chunkId());
  private readonly workItemsQuery = injectHubChunkWorkItemsQuery(() => this.chunkId());
  private readonly answerMutation = injectAnswerQuestionMutation();
  private readonly resolveMutation = injectResolveDecisionMutation();
  private readonly detachMutation = injectDetachChunkMutation();
  private readonly pauseMutation = injectChunkPauseMutation();
  private readonly editGraphMutation = injectSetChunkGraphMutation();

  /** The open chunk's last operator-action failure, or `null`. Reset on every new
   * attempt and whenever a different chunk opens (issue #42). Shared by every action
   * in the dock — detach, pause, resume (issue #46). */
  protected readonly actionError = signal<string | null>(null);

  /** The open chunk's last operator-action **outcome** — a non-failure result that still
   * needs saying (issue #165). Today that is exactly one case: a lost answer race, where
   * the hub's 409 carries the *winning* answer. It is a channel of its own rather than a
   * second use of {@link actionError} because the two read differently to an operator —
   * "someone beat you to it, here is what they said" is news, not a failure to retry. */
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

  /** The open chunk's related work items + fetch state for the Issue tab (issue #24). A failed
   * read (unreachable hub / no work-source) becomes `error` so the tab shows a visible notice. */
  protected readonly workItems = computed<WorkItemsState>(() => {
    if (this.chunkId() === null) return { status: 'loading', items: [] };
    if (this.workItemsQuery.isError()) return { status: 'error', items: [] };
    if (this.workItemsQuery.isPending()) return { status: 'loading', items: [] };
    return { status: 'success', items: this.workItemsQuery.data()?.items ?? [] };
  });

  /** Answer an open question. A lost first-write-wins race comes back as a 409 whose body
   * is the *winning* answer, so it is reported as an outcome naming the winner rather than
   * folded through `errorMessage()` into a generic failure (issue #165); any other failure
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
    this.resolveMutation.mutate({ decisionId: event.decisionId, choice: event.choice, chunkId: event.chunkId });
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

  protected onEditGraph(event: EditGraphEvent): void {
    this.beginAction();
    this.editGraphMutation.mutate(
      { chunkId: event.chunkId, graphId: event.graphId },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Set graph failed.')) },
    );
  }
}

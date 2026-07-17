import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal } from '@angular/core';

import { injectHubChunkDetailQuery } from '../chunks/chunk-detail.query';
import { injectHubChunkPmItemsQuery } from '../chunks/chunk-pm-items.query';
import { injectDetachChunkMutation } from '../chunks/detach.mutations';
import { injectAnswerQuestionMutation, injectResolveDecisionMutation } from '../chunks/human.mutations';
import { injectChunkPauseMutation } from '../chunks/pause.mutations';
import {
  type AnswerQuestionEvent,
  ChunkDetailPanel,
  type PmItemsState,
  type ResolveDecisionEvent,
} from './chunk-detail-panel';

/** The hub's `{"detail": "..."}` error body, or anything close enough to read one
 * off of — 404/409 aren't in the generated error union (only 422 is documented),
 * so this reads the same shape defensively rather than trusting the response type.
 * `fallback` names the verb that failed, for the case where no body can be read. */
function errorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'detail' in error && typeof error.detail === 'string') {
    return error.detail;
  }
  return fallback;
}

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
 * detaching, or pausing/resuming invalidates the chunk and the fleet list, and the SSE
 * stream corroborates. Every operator action's 404/409 is read off its mutation's
 * `onError` and held in the shared `actionError` for the panel to show — issue #42's
 * "report, don't swallow" requirement, which issue #46's pause/resume follows rather
 * than reinvent — and clears on the next attempt or the moment a different chunk opens.
 */
@Component({
  selector: 'fleet-chunk-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkDetailPanel],
  template: `
    @if (detail(); as d) {
      <fleet-chunk-detail-panel
        [detail]="d"
        [pmItems]="pmItems()"
        [actionError]="actionError()"
        (dismiss)="dismiss.emit()"
        (answerQuestion)="onAnswer($event)"
        (resolveDecision)="onResolve($event)"
        (detach)="onDetach($event)"
        (pauseChunk)="onPause($event)"
        (resumeChunk)="onResume($event)"
      />
    } @else {
      <p class="rest" data-testid="chunk-detail-empty">
        {{ chunkId() === null ? 'SELECT A CHUNK TO SEE ITS HISTORY & ARTIFACTS' : 'LOADING…' }}
      </p>
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
  private readonly pmItemsQuery = injectHubChunkPmItemsQuery(() => this.chunkId());
  private readonly answerMutation = injectAnswerQuestionMutation();
  private readonly resolveMutation = injectResolveDecisionMutation();
  private readonly detachMutation = injectDetachChunkMutation();
  private readonly pauseMutation = injectChunkPauseMutation();

  /** The open chunk's last operator-action failure, or `null`. Reset on every new
   * attempt and whenever a different chunk opens (issue #42). Shared by every action
   * in the dock — detach, pause, resume (issue #46). */
  protected readonly actionError = signal<string | null>(null);

  constructor() {
    effect(() => {
      this.chunkId();
      this.actionError.set(null);
    });
  }

  /** The open chunk's aggregate, or `undefined` while closed / still loading. */
  protected readonly detail = computed(() => (this.chunkId() === null ? undefined : this.detailQuery.data()));

  /** The open chunk's related PM items + fetch state for the Issue tab (issue #24). A failed
   * read (unreachable hub / no work-source) becomes `error` so the tab shows a visible notice. */
  protected readonly pmItems = computed<PmItemsState>(() => {
    if (this.chunkId() === null) return { status: 'loading', items: [] };
    if (this.pmItemsQuery.isError()) return { status: 'error', items: [] };
    if (this.pmItemsQuery.isPending()) return { status: 'loading', items: [] };
    return { status: 'success', items: this.pmItemsQuery.data()?.items ?? [] };
  });

  protected onAnswer(event: AnswerQuestionEvent): void {
    this.answerMutation.mutate({ questionId: event.questionId, answer: event.answer, chunkId: event.chunkId });
  }

  protected onResolve(event: ResolveDecisionEvent): void {
    this.resolveMutation.mutate({ decisionId: event.decisionId, choice: event.choice, chunkId: event.chunkId });
  }

  protected onDetach(chunkId: string): void {
    this.actionError.set(null);
    this.detachMutation.mutate(
      { chunkId },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Detach failed.')) },
    );
  }

  protected onPause(chunkId: string): void {
    this.actionError.set(null);
    this.pauseMutation.mutate(
      { chunkId, paused: true },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Pause failed.')) },
    );
  }

  protected onResume(chunkId: string): void {
    this.actionError.set(null);
    this.pauseMutation.mutate(
      { chunkId, paused: false },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Resume failed.')) },
    );
  }
}

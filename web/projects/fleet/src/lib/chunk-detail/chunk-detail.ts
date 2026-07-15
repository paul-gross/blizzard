import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { injectHubChunkDetailQuery } from '../chunks/chunk-detail.query';
import { injectAnswerQuestionMutation, injectResolveDecisionMutation } from '../chunks/human.mutations';
import {
  type AnswerQuestionEvent,
  ChunkDetailPanel,
  type ResolveDecisionEvent,
} from './chunk-detail-panel';

/**
 * The chunk detail **container** — owns the reactive detail query (D-036) and the
 * human-loop mutations (answer a question, resolve a gate decision — D-042/D-052),
 * and renders the presentational {@link ChunkDetailPanel} over them. It stays
 * mounted in the bottom dock and shows a rest state until a card is selected; the
 * panel stays presentational and every server call goes through the generated
 * client (bzh:generated-client).
 *
 * Reactive over the selected `chunkId`: the query re-keys and disables itself while
 * nothing is open, so no request fires for the empty board. Answering or resolving
 * invalidates the chunk and the fleet list, and the SSE stream corroborates.
 */
@Component({
  selector: 'fleet-chunk-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkDetailPanel],
  template: `
    @if (detail(); as d) {
      <fleet-chunk-detail-panel
        [detail]="d"
        (dismiss)="dismiss.emit()"
        (answerQuestion)="onAnswer($event)"
        (resolveDecision)="onResolve($event)"
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
      font-size: 11px;
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
  private readonly answerMutation = injectAnswerQuestionMutation();
  private readonly resolveMutation = injectResolveDecisionMutation();

  /** The open chunk's aggregate, or `undefined` while closed / still loading. */
  protected readonly detail = computed(() => (this.chunkId() === null ? undefined : this.detailQuery.data()));

  protected onAnswer(event: AnswerQuestionEvent): void {
    this.answerMutation.mutate({ questionId: event.questionId, answer: event.answer, chunkId: event.chunkId });
  }

  protected onResolve(event: ResolveDecisionEvent): void {
    this.resolveMutation.mutate({ decisionId: event.decisionId, choice: event.choice, chunkId: event.chunkId });
  }
}

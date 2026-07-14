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
 * and renders the presentational {@link ChunkDetailPanel} over them. This is the
 * data seam the board opens when a card is selected; the panel stays presentational
 * and every server call goes through the generated client (bzh:generated-client).
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
    }
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
    }
  `,
})
export class ChunkDetail {
  /** The selected chunk id, or `null` when the drawer is closed. */
  readonly chunkId = input<string | null>(null);

  /** Emitted when the operator dismisses the drawer. */
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

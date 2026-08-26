import { ChangeDetectionStrategy, Component, computed, input, output, viewChild } from '@angular/core';

import type { ChunkDetail, ChunkStatus, DecisionView, QuestionView } from '../api/hub';
import { KitButton } from '../kit/kit-button';
import { ChunkEscalation } from './chunk-escalation';
import { ChunkGateDocket } from './chunk-gate-docket';

/** Emitted when the operator answers a chunk's open question from the dock. */
export interface AnswerQuestionEvent {
  readonly questionId: string;
  readonly answer: string;
  readonly chunkId: string;
}

/** Emitted when the operator resolves a chunk's open gate decision from the dock.
 * `struck` names the docket proposals toggled to refuse (blizzard#367); empty passes
 * every proposal. */
export interface ResolveDecisionEvent {
  readonly decisionId: string;
  readonly choice: string;
  readonly chunkId: string;
  readonly struck: readonly string[];
}

/** How many recently answered questions the dock keeps a trail for (issue #165). */
const ANSWERED_TRAIL_LIMIT = 3;

/** The statuses a chunk never leaves. An answer still undelivered on one of these will
 * never be delivered — nothing is left to resume — so the trail says that rather than
 * showing an in-flight state forever. */
const TERMINAL_STATUSES: ReadonlySet<string> = new Set<ChunkStatus>(['done', 'stopped']);

/**
 * The chunk's awaiting-human gate (issue #79) — whatever the chunk waits on
 * a human for: an open **question** with an inline **Answer** action (MVP
 * criterion 7), an open gate **decision** as **choice buttons** (MVP
 * criterion 12), or an open **escalation**, rendered by {@link ChunkEscalation}
 * — this component keeps no escalation state of its own, just forwards `detail`.
 *
 * Below those, the **answered trail** (issue #165): a recently answered question stays
 * rendered with who answered it, what they said, and whether the runner has delivered
 * the answer into the resumed session — the return leg of the rendezvous, so the person
 * who answered sees it arrive instead of watching the row disappear.
 *
 * Presentational only: it holds the detail input and emits `answerQuestion`
 * / `resolveDecision`; the mutations those events drive live in the
 * container.
 */
@Component({
  selector: 'fleet-chunk-detail-awaiting-human',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkEscalation, ChunkGateDocket, KitButton],
  templateUrl: './chunk-awaiting-human.html',
  styleUrl: './chunk-awaiting-human.css',
})
export class ChunkAwaitingHuman {
  /** The mounted docket, when the gate is open — its own toggle set is read here at
   * resolve time rather than round-tripped back up through an output. */
  private readonly docket = viewChild(ChunkGateDocket);
  /** The chunk aggregate to render (open questions, gate decision, escalation). */
  readonly detail = input.required<ChunkDetail>();

  /** Whether the current identity may answer an open question (`question:answer` —
   * issue #210). Withholds the answer input/chips when `false`, though the question
   * text itself still shows — a `guest` reads that a chunk is waiting, just cannot
   * act on it. `null`/pending resolves to `false` (hidden until confirmed). */
  readonly canAnswer = input(false);

  /** Whether the current identity may resolve an open gate decision (`gate:resolve` —
   * issue #210). Withholds the choice chips when `false`; `null`/pending resolves to
   * `false`. */
  readonly canResolve = input(false);

  /** Emitted when the operator answers an open question (MVP criterion 7). */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision. */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** The chunk's open (unanswered) questions — the ask a parked chunk waits on. */
  protected readonly openQuestions = computed<readonly QuestionView[]>(() =>
    (this.detail().questions ?? []).filter((q) => !q.answered),
  );

  /**
   * The chunk's recently answered questions, most recently **answered** first — the
   * return trail (issue #165). Answering used to drop the row from the dock the instant
   * it landed, which left an operator answering from a phone with no evidence their
   * answer went anywhere; keeping it renders who answered, what they said, and whether
   * the runner has delivered it into the resumed session.
   *
   * Sorted on `answered_at`, not on the hub's own order — that list is by `asked_at`
   * (`chunk_store.load_questions`), and the two disagree whenever asks and answers
   * interleave. Since the whole question this panel answers is "did *my* answer just
   * land", ordering by when it was *asked* can push the row the operator is looking for
   * out of the cap entirely.
   *
   * Capped at {@link ANSWERED_TRAIL_LIMIT} because this is a *recency* affordance, not
   * a history: a chunk that asked its way through a long build would otherwise bury the
   * live ask under every answer it ever got. The cap is presentational only — the chunk
   * read carries every question, so nothing here is the record.
   */
  protected readonly answeredQuestions = computed<readonly QuestionView[]>(() =>
    // `filter` already copied, so sorting in place does not mutate the query's data.
    (this.detail().questions ?? [])
      .filter((q) => q.answered)
      .sort((a, b) => (b.answered_at ?? '').localeCompare(a.answered_at ?? ''))
      .slice(0, ANSWERED_TRAIL_LIMIT),
  );

  /**
   * The delivery leg of one answered question's trail.
   *
   * Three states, not two. An answer that has not been delivered is only *in flight*
   * while the chunk can still resume — a question answered after its runner went down,
   * or on a chunk since reaped or taken over, has no delivery row and never will. A
   * two-state ternary reads the present-progressive "Delivering…" forever there, which
   * is the one place this trail would assert something false rather than merely stale:
   * it promises a return trip nothing will complete. On a terminal chunk it says so.
   */
  protected deliveryLine(question: QuestionView): string {
    if (question.delivered) return 'Delivered · agent resumed';
    return TERMINAL_STATUSES.has(this.detail().status)
      ? 'Not delivered — the chunk ended first'
      : 'Delivering to the agent…';
  }

  /** The chunk's live gate decision while it still awaits the resolving transition. */
  protected readonly openDecision = computed<DecisionView | null>(() => {
    const decision = this.detail().decision;
    return decision && !decision.transitioned ? decision : null;
  });

  /** Emit an answer for a question — no-op on an empty answer. */
  protected submitAnswer(questionId: string, answer: string): void {
    const trimmed = answer.trim();
    if (!trimmed) return;
    this.answerQuestion.emit({ questionId, answer: trimmed, chunkId: this.detail().chunk_id });
  }

  /** Emit a resolution for the open gate decision, carrying whatever the docket's
   * toggle set currently holds. */
  protected resolve(decisionId: string, choice: string): void {
    this.resolveDecision.emit({
      decisionId,
      choice,
      chunkId: this.detail().chunk_id,
      struck: this.docket()?.struckIds() ?? [],
    });
  }
}

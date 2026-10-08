import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type { ChunkDetail, DecisionView, QuestionView } from '../../api/hub';
import { KitButton } from '../../kit/kit-button';
import { KitChips, type KitChipOption } from '../../kit/kit-chips';
import { KitTextInput } from '../../kit/kit-text-input';
import { FleetWhen } from '../../core/when-display/fleet-when';
import { runnerDisplayName } from '../../core/runner-display-name';
import { ChunkEscalation } from './chunk-escalation';

/** Emitted when the operator answers a chunk's open question from the dock. */
export interface AnswerQuestionEvent {
  readonly questionId: string;
  readonly answer: string;
  readonly chunkId: string;
}

/** Emitted when the operator resolves a chunk's open gate decision from the dock. */
export interface ResolveDecisionEvent {
  readonly decisionId: string;
  readonly choice: string;
  readonly chunkId: string;
}

/** How many recently answered questions the dock keeps a trail for. */
const ANSWERED_TRAIL_LIMIT = 3;


/**
 * The chunk's awaiting-human gate — whatever the chunk waits on
 * a human for: an open **question** with an inline **Answer** action (MVP
 * criterion 7), an open gate **decision** as **choice buttons** (MVP
 * criterion 12), or an open **escalation**, rendered by {@link ChunkEscalation}
 * — this component keeps no escalation state of its own, just forwards `detail`.
 *
 * Below those, the **answered trail**: a recently answered question stays
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
  imports: [ChunkEscalation, FleetWhen, KitButton, KitChips, KitTextInput],
  templateUrl: './chunk-awaiting-human.html',
  styleUrl: './chunk-awaiting-human.css',
})
export class ChunkAwaitingHuman {
  protected questionOptions(options: readonly string[]): readonly KitChipOption[] {
    return options.map((value) => ({ value, label: value, testid: 'question-option' }));
  }

  protected decisionOptions(choices: readonly { name: string; description: string }[]): readonly KitChipOption[] {
    return choices.map((choice) => ({ value: choice.name, label: choice.name, title: choice.description, testid: 'decision-choice' }));
  }
  /** The chunk aggregate to render (open questions, gate decision, escalation). */
  readonly detail = input.required<ChunkDetail>();

  /** Whether the current identity may answer an open question (`question:answer`).
   * Withholds the answer input/chips when `false`, though the question
   * text itself still shows — a `guest` reads that a chunk is waiting, just cannot
   * act on it. `null`/pending resolves to `false` (hidden until confirmed). */
  readonly canAnswer = input(false);

  /** Whether the current identity may resolve an open gate decision (`gate:resolve`).
   * Withholds the choice chips when `false`; `null`/pending resolves to
   * `false`. */
  readonly canResolve = input(false);

  /** Whether the resolve-decision mutation is in flight — disables the choice chips so
   * a double click cannot resolve the gate twice. */
  readonly resolvePending = input(false);

  /** The ids of the questions an answer mutation is in flight for — disables just those
   * questions' option chips and Answer button, so a double click cannot submit the same
   * question twice while the chunk's other questions stay answerable. */
  readonly pendingAnswerQuestionIds = input<readonly string[]>([]);

  /** Emitted when the operator answers an open question (MVP criterion 7). */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision. */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** Each open question's in-progress answer, keyed by `question_id` — `KitTextInput`
   * is a controlled control, so
   * this dock holds the live draft itself rather than reading a template-ref'd
   * `<input>`'s `.value` at submit time, one draft per question since several can be
   * open at once. */
  private readonly answerDrafts = signal<Readonly<Partial<Record<string, string>>>>({});

  /** A gate-imposing runner's display name. */
  protected readonly runnerDisplayName = runnerDisplayName;

  /** The chunk's open (unanswered) questions — the ask a parked chunk waits on. */
  protected readonly openQuestions = computed<readonly QuestionView[]>(() =>
    (this.detail().questions ?? []).filter((q) => !q.answered),
  );

  /**
   * The chunk's recently answered questions, most recently **answered** first — the
   * return trail, so an operator who answered has evidence their answer went
   * somewhere: who answered, what they said, and whether the runner has delivered it
   * into the resumed session.
   *
   * Sorted on `answered_at`, not on the hub's own order — that list is by `asked_at`,
   * and the two disagree whenever asks and answers
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
   * it promises a return trip nothing will complete. On a terminal chunk (the wire's
   * `terminal` — a status the chunk never leaves, so nothing is left to resume) it says so.
   */
  protected deliveryLine(question: QuestionView): string {
    if (question.delivered) return 'Delivered · agent resumed';
    return this.detail().terminal
      ? 'Not delivered — the chunk ended first'
      : 'Delivering to the agent…';
  }

  /** The chunk's live gate decision — not yet resolved, so its choices are still offered. */
  protected readonly openDecision = computed<DecisionView | null>(() => {
    const decision = this.detail().decision;
    return decision && !decision.transitioned && !decision.resolved_choice ? decision : null;
  });

  /** The chunk's gate decision once resolved but before the runner records the resolving
   * transition — rendered as who chose what, and when, in place of the choices. Once the
   * transition lands the chunk detail stops carrying it. */
  protected readonly resolvedDecision = computed<DecisionView | null>(() => {
    const decision = this.detail().decision;
    return decision && !decision.transitioned && decision.resolved_choice ? decision : null;
  });

  /** The live draft for one question's answer field — `''` for a question the
   * operator has not typed into yet. */
  protected answerDraft(questionId: string): string {
    return this.answerDrafts()[questionId] ?? '';
  }

  /** Records a question's answer field as the operator types — carried up from
   * `KitTextInput`'s `valueChange`. */
  protected onAnswerInput(questionId: string, value: string): void {
    this.answerDrafts.update((drafts) => ({ ...drafts, [questionId]: value }));
  }

  /** Emit an answer for a question — no-op on an empty answer — and clear its draft. */
  protected submitAnswer(questionId: string, answer: string): void {
    const trimmed = answer.trim();
    if (!trimmed) return;
    this.answerQuestion.emit({ questionId, answer: trimmed, chunkId: this.detail().chunk_id });
    this.answerDrafts.update((drafts) => {
      const next = { ...drafts };
      delete next[questionId];
      return next;
    });
  }

  /** Emit a resolution for the open gate decision. */
  protected resolve(decisionId: string, choice: string): void {
    this.resolveDecision.emit({
      decisionId,
      choice,
      chunkId: this.detail().chunk_id,
    });
  }
}

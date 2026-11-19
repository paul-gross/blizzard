import type { SaveOutcome } from '../../core/forms/outcome';

/** One question a chunk's worker asked a human — open until answered, then part of the answered trail. */
export interface AwaitingQuestion {
  readonly question_id: string;
  readonly question: string;
  /** The answers the worker offered to pick from; empty when it asked for free text. */
  readonly options: readonly string[];
  readonly answered: boolean;
  readonly answer: string | null;
  readonly answered_at: string | null;
  readonly answered_by: string | null;
  /** Whether the runner has carried the answer back into the resumed session. */
  readonly delivered: boolean;
  /** Whether a restart answered the question for the system and ended its session. */
  readonly superseded: boolean;
}

/** One check the runner ran at the gated step's exit. */
export interface GateCheck {
  readonly command: string;
  readonly passed: boolean;
}

/** One choice a gate offers. */
export interface GateChoice {
  readonly name: string;
  readonly description: string;
  /** Whether picking this choice would override a red check — the hub's judgment. */
  readonly would_override_red_checks: boolean;
}

/** A chunk's gate decision — live while unresolved, then resolved until its transition is recorded. */
export interface AwaitingDecision {
  readonly decision_id: string;
  readonly node_name: string;
  /** The runner whose own configuration imposed the gate; `null` when the graph declared it. */
  readonly imposed_by_runner_id: string | null;
  readonly imposed_by_runner_name: string | null;
  readonly check_results: readonly GateCheck[];
  readonly choices: readonly GateChoice[];
  readonly transitioned: boolean;
  readonly resolved_choice: string | null;
  readonly resolved_by: string | null;
  readonly resolved_at: string | null;
  readonly overrode_red_checks: boolean;
  readonly note: string | null;
}

/** What a chunk waits on a human for, as the awaiting-human views read it. */
export interface AwaitingHuman {
  readonly chunk_id: string;
  /** Whether the chunk is in a status it never leaves, so nothing is left to resume. */
  readonly terminal: boolean;
  /** The unanswered questions — the ask a parked chunk waits on. */
  readonly open_questions: readonly AwaitingQuestion[];
  /** The recently answered questions, most recently answered first, capped at {@link ANSWERED_TRAIL_LIMIT}. */
  readonly answered_trail: readonly AwaitingQuestion[];
  /** The gate decision while its choices are still offered. */
  readonly live_decision: AwaitingDecision | null;
  /** The gate decision once resolved but before the runner records the resolving transition. */
  readonly resolved_decision: AwaitingDecision | null;
}

/** The parts {@link awaitingHuman} judges the aggregate from. */
export interface AwaitingHumanParts {
  readonly chunk_id: string;
  readonly terminal: boolean;
  readonly questions: readonly AwaitingQuestion[];
  readonly decision: AwaitingDecision | null;
}

/** How many recently answered questions the trail keeps. The cap is presentational only. */
export const ANSWERED_TRAIL_LIMIT = 3;

/**
 * The aggregate from a chunk's questions and gate decision: the open/answered split, the trail's
 * order and cap, and the live/resolved split of the decision. A transitioned decision is neither.
 */
export function awaitingHuman(parts: AwaitingHumanParts): AwaitingHuman {
  const decision = parts.decision && !parts.decision.transitioned ? parts.decision : null;
  return {
    chunk_id: parts.chunk_id,
    terminal: parts.terminal,
    open_questions: parts.questions.filter((q) => !q.answered),
    // `filter` already copied, so sorting in place does not mutate the caller's list.
    answered_trail: parts.questions
      .filter((q) => q.answered)
      .sort((a, b) => (b.answered_at ?? '').localeCompare(a.answered_at ?? ''))
      .slice(0, ANSWERED_TRAIL_LIMIT),
    live_decision: decision && !decision.resolved_choice ? decision : null,
    resolved_decision: decision?.resolved_choice ? decision : null,
  };
}

/**
 * The delivery leg of one answered question's trail.
 *
 * Four states, not two. A restart answers every open question for the system and ends its session,
 * so a superseded answer is named for that cause, ahead of any terminal line.
 *
 * An answer that has not been delivered is otherwise only *in flight* while the chunk can still
 * resume — a question answered after its runner went down, or on a chunk since reaped or taken
 * over, has no delivery row and never will. A two-state ternary reads the present-progressive
 * "Delivering…" forever there, promising a return trip nothing will complete. On a terminal chunk
 * it says so.
 */
export function deliveryLine(question: AwaitingQuestion, terminal: boolean): string {
  if (question.delivered) return 'Delivered · agent resumed';
  if (question.superseded) return 'Not delivered — the session was restarted';
  return terminal ? 'Not delivered — the chunk ended first' : 'Delivering to the agent…';
}

/** One open question's answer as its form states it, as typed. */
export interface AnswerDraft {
  readonly answer: string;
}

/** An open gate's optional note as its form states it, as typed; sent with whichever choice is picked. */
export interface DecisionNoteDraft {
  readonly note: string;
}

export const EMPTY_ANSWER_DRAFT: AnswerDraft = { answer: '' };

export const EMPTY_DECISION_NOTE_DRAFT: DecisionNoteDraft = { note: '' };

/**
 * How an app saves what the awaiting-human forms state. Each call names the chunk, since the saves re-read it.
 * A refusal is answered as a `SaveOutcome`, never thrown; an outcome that closes the question or gate anyway,
 * such as losing a race to another answer, is the app's to report and answers ok.
 */
export interface AwaitingHumanFormActions {
  answer(chunkId: string, questionId: string, draft: AnswerDraft): Promise<SaveOutcome<AnswerDraft>>;
  resolve(chunkId: string, decisionId: string, choice: string, draft: DecisionNoteDraft): Promise<SaveOutcome<DecisionNoteDraft>>;
}

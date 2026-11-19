import type { ChunkDetail, DecisionView, QuestionView } from '../../api/hub';
import { type AwaitingDecision, type AwaitingHuman, type AwaitingQuestion, awaitingHuman } from './awaiting-human.domain';

function fromQuestionView(wire: QuestionView): AwaitingQuestion {
  return {
    question_id: wire.question_id,
    question: wire.question,
    options: wire.options,
    answered: wire.answered,
    answer: wire.answer,
    answered_at: wire.answered_at,
    answered_by: wire.answered_by,
    delivered: wire.delivered,
    superseded: wire.superseded,
  };
}

function fromDecisionView(wire: DecisionView): AwaitingDecision {
  return {
    decision_id: wire.decision_id,
    node_name: wire.node_name,
    imposed_by_runner_id: wire.imposed_by_runner_id,
    imposed_by_runner_name: wire.imposed_by_runner_name,
    check_results: wire.check_results.map((c) => ({ command: c.command, passed: c.passed })),
    choices: wire.choices.map((c) => ({
      name: c.name,
      description: c.description,
      would_override_red_checks: c.would_override_red_checks,
    })),
    transitioned: wire.transitioned,
    resolved_choice: wire.resolved_choice,
    resolved_by: wire.resolved_by,
    resolved_at: wire.resolved_at,
    overrode_red_checks: wire.overrode_red_checks,
    note: wire.note,
  };
}

/** A chunk aggregate as what it waits on a human for. */
export function toAwaitingHuman(detail: ChunkDetail): AwaitingHuman {
  return awaitingHuman({
    chunk_id: detail.chunk_id,
    terminal: detail.terminal,
    questions: (detail.questions ?? []).map(fromQuestionView),
    decision: detail.decision ? fromDecisionView(detail.decision) : null,
  });
}

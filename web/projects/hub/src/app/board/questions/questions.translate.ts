import type { QuestionView } from 'fleet/hub-api';
import type { BoardQuestionPresentation } from './questions.domain';

export function fromBoardQuestion(question: QuestionView): BoardQuestionPresentation {
  return {
    chunk_id: question.chunk_id,
    question: question.question,
    question_id: question.question_id,
    runner_id: question.runner_id,
    runner_name: question.runner_name,
    options: question.options ?? [],
  };
}

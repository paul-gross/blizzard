import type { QuestionView } from 'fleet';
import type { BoardQuestion } from './questions.domain';

export function fromBoardQuestion(question: QuestionView): BoardQuestion {
  return { chunk_id: question.chunk_id, question: question.question };
}

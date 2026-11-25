import type { QuestionView } from 'fleet';
import { fromBoardQuestion } from './questions.translate';

describe('fromBoardQuestion', () => {
  it('keeps the chunk and question while leaving transport-only fields behind', () => {
    const wire = { chunk_id: 'ch_1', question: 'Which branch?', question_id: 'qn_1' } as QuestionView;
    expect(fromBoardQuestion(wire)).toEqual({ chunk_id: 'ch_1', question: 'Which branch?' });
  });
});

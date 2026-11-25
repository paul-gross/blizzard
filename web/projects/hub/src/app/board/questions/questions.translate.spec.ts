import { questionView } from 'fleet/testing';
import { fromBoardQuestion } from './questions.translate';

describe('fromBoardQuestion', () => {
  it('keeps the rail and glance facts while leaving transport-only fields behind', () => {
    const wire = questionView({ chunk_id: 'ch_1', question: 'Which branch?', question_id: 'qn_1', runner_id: 'rn_1', runner_name: 'runner', options: ['main'] });
    expect(fromBoardQuestion(wire)).toEqual({ chunk_id: 'ch_1', question: 'Which branch?', question_id: 'qn_1', runner_id: 'rn_1', runner_name: 'runner', options: ['main'] });
  });

  it('presents absent options as an empty list', () => {
    expect(fromBoardQuestion(questionView({ options: undefined })).options).toEqual([]);
  });
});

import { chunkDetail, decisionView, questionView } from 'fleet/testing';
import { toAwaitingHuman } from './awaiting-human.translate';

describe('toAwaitingHuman', () => {
  it('reads the chunk id, terminal flag, questions and decision off the aggregate', () => {
    const state = toAwaitingHuman(
      chunkDetail({
        chunk_id: 'ch_1',
        terminal: true,
        questions: [
          questionView({ question_id: 'q_open', question: 'Which?', options: ['a', 'b'] }),
          questionView({ question_id: 'q_done', answered: true, answer: 'a', answered_by: 'alice', answered_at: '2026-07-13T00:01:00Z', delivered: true }),
        ],
        decision: decisionView({ decision_id: 'dc_1', imposed_by_runner_id: 'rn_1', imposed_by_runner_name: 'r', note: 'n' }),
      }),
    );

    expect(state.chunk_id).toBe('ch_1');
    expect(state.terminal).toBe(true);
    expect(state.open_questions).toEqual([
      {
        question_id: 'q_open', question: 'Which?', options: ['a', 'b'], answered: false, answer: null,
        answered_at: null, answered_by: null, delivered: false, superseded: false,
      },
    ]);
    expect(state.answered_trail.map((q) => [q.question_id, q.answer, q.answered_by, q.delivered])).toEqual([['q_done', 'a', 'alice', true]]);
    expect(state.live_decision).toEqual({
      decision_id: 'dc_1',
      node_name: 'review',
      imposed_by_runner_id: 'rn_1',
      imposed_by_runner_name: 'r',
      check_results: [{ command: 'npm run lint', passed: true }, { command: 'npm run test', passed: false }],
      choices: [
        { name: 'approve', description: 'Accept the change and move on.', would_override_red_checks: true },
        { name: 'rework', description: 'Send the change back to build.', would_override_red_checks: false },
      ],
      transitioned: false,
      resolved_choice: null,
      resolved_by: null,
      resolved_at: null,
      overrode_red_checks: false,
      note: 'n',
    });
  });

  it('reads an aggregate with no questions or decision as waiting on nothing', () => {
    const state = toAwaitingHuman(chunkDetail({ questions: undefined, decision: null }));
    expect(state).toMatchObject({ open_questions: [], answered_trail: [], live_decision: null, resolved_decision: null });
  });
});

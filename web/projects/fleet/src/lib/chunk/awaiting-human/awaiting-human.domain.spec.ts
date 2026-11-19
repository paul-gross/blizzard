import { answeredQuestion, awaitingDecision, awaitingQuestion } from 'fleet/testing';
import { ANSWERED_TRAIL_LIMIT, awaitingHuman, deliveryLine } from './awaiting-human.domain';

const parts = { chunk_id: 'ch_1', terminal: false, questions: [], decision: null };

describe('awaitingHuman', () => {
  it('splits the open questions from the answered trail', () => {
    const open = awaitingQuestion({ question_id: 'q_open' });
    const done = answeredQuestion({ question_id: 'q_done' });
    const state = awaitingHuman({ ...parts, questions: [done, open] });
    expect(state.open_questions).toEqual([open]);
    expect(state.answered_trail).toEqual([done]);
  });

  it('orders the trail by when each was answered, newest first, and caps it', () => {
    const questions = ['2026-07-13T00:09:00Z', '2026-07-13T00:02:00Z', '2026-07-13T00:03:00Z', '2026-07-13T00:01:00Z'].map(
      (at, i) => answeredQuestion({ question_id: `q${i + 1}`, answered_at: at }),
    );
    const trail = awaitingHuman({ ...parts, questions }).answered_trail.map((q) => q.question_id);
    expect(trail).toEqual(['q1', 'q3', 'q2']);
    expect(trail).toHaveLength(ANSWERED_TRAIL_LIMIT);
  });

  it('reads an unresolved decision as live and a resolved one as resolved', () => {
    const live = awaitingDecision();
    expect(awaitingHuman({ ...parts, decision: live })).toMatchObject({ live_decision: live, resolved_decision: null });
    const resolved = awaitingDecision({ resolved_choice: 'approve' });
    expect(awaitingHuman({ ...parts, decision: resolved })).toMatchObject({ live_decision: null, resolved_decision: resolved });
  });

  it('reads a transitioned decision as neither', () => {
    const state = awaitingHuman({ ...parts, decision: awaitingDecision({ transitioned: true, resolved_choice: 'approve' }) });
    expect(state.live_decision).toBeNull();
    expect(state.resolved_decision).toBeNull();
  });
});

describe('deliveryLine', () => {
  it('names each of the four delivery states', () => {
    expect(deliveryLine(answeredQuestion({ delivered: true }), true)).toBe('Delivered · agent resumed');
    expect(deliveryLine(answeredQuestion({ superseded: true }), true)).toBe('Not delivered — the session was restarted');
    expect(deliveryLine(answeredQuestion(), true)).toBe('Not delivered — the chunk ended first');
    expect(deliveryLine(answeredQuestion(), false)).toBe('Delivering to the agent…');
  });
});

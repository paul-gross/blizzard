import { readRefusal } from 'fleet/core';

import { ANSWER_REFUSALS, DECISION_NOTE_REFUSALS, toAnswerVars, toResolveVars } from './awaiting-human.translate';

describe('toAnswerVars', () => {
  it('sends the trimmed answer with its question and chunk', () => {
    expect(toAnswerVars('ch_1', 'qn_1', { answer: '  rest ' })).toEqual({ chunkId: 'ch_1', questionId: 'qn_1', answer: 'rest' });
  });
});

describe('toResolveVars', () => {
  it('sends the trimmed note with the choice, its decision, and chunk', () => {
    expect(toResolveVars('ch_1', 'de_1', 'approve', { note: ' careful ' })).toEqual({
      chunkId: 'ch_1',
      decisionId: 'de_1',
      choice: 'approve',
      note: 'careful',
    });
  });

  it('omits a blank note', () => {
    expect(toResolveVars('ch_1', 'de_1', 'approve', { note: '   ' })).not.toHaveProperty('note');
  });
});

describe('ANSWER_REFUSALS', () => {
  const read = (error: unknown) => readRefusal(error, ANSWER_REFUSALS, 'Answer failed.');

  it('lands the answer validation path on the answer field', () => {
    expect(read({ detail: [{ loc: ['body', 'answer'], msg: 'too long' }] })).toEqual({
      ok: false,
      fields: { answer: 'too long' },
      form: null,
    });
  });

  it('lands an unknown question and any other refusal on the form line', () => {
    expect(read({ detail: 'unknown question qn_1' })).toEqual({ ok: false, fields: {}, form: 'unknown question qn_1' });
    expect(read({ detail: [{ loc: ['body', 'answered_by'], msg: 'blank' }] })).toEqual({
      ok: false,
      fields: {},
      form: 'answered_by: blank',
    });
  });
});

describe('DECISION_NOTE_REFUSALS', () => {
  const read = (error: unknown) => readRefusal(error, DECISION_NOTE_REFUSALS, 'Resolve failed.');

  it('lands the note validation path on the note field', () => {
    expect(read({ detail: [{ loc: ['body', 'note'], msg: 'too long' }] })).toEqual({
      ok: false,
      fields: { note: 'too long' },
      form: null,
    });
  });

  it('lands a choice the gate does not offer on the form line', () => {
    expect(read({ detail: "choice 'maybe' is not one of approve, reject" })).toEqual({
      ok: false,
      fields: {},
      form: "choice 'maybe' is not one of approve, reject",
    });
  });
});

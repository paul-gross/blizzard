import { describe, expect, it } from 'vitest';
import { runnerAskView } from 'fleet/testing';

import { fromAskView } from './ask.translate';

describe('fromAskView', () => {
  it('carries the question, its options and where it was asked', () => {
    const wire = runnerAskView({ question_id: 'q-7', project_id: 'proj_1', session_id: 's-1', question: 'Ship?', options: ['yes', 'no'] });
    expect(fromAskView(wire)).toEqual({
      question_id: 'q-7',
      chunk_id: 'ch_1',
      project_id: 'proj_1',
      lease_id: 'L-1',
      session_id: 's-1',
      harness_id: null,
      question: 'Ship?',
      options: ['yes', 'no'],
      asked_at: wire.asked_at,
    });
  });
});

import type { DecisionView } from 'fleet';
import { fromBoardDecision } from './gates.translate';

describe('fromBoardDecision', () => {
  it('keeps the chunk without exposing the decision transport', () => {
    const wire = { chunk_id: 'ch_1', decision_id: 'de_1' } as DecisionView;
    expect(fromBoardDecision(wire)).toEqual({ chunk_id: 'ch_1' });
  });
});

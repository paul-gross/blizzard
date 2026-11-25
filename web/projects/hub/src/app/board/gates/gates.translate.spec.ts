import { decisionView } from 'fleet/testing';
import { fromBoardDecision } from './gates.translate';

describe('fromBoardDecision', () => {
  it('keeps the rail and glance facts without exposing the decision transport', () => {
    const wire = decisionView({ chunk_id: 'ch_1', decision_id: 'de_1', node_name: 'approve', imposed_by_runner_id: 'rn_1', imposed_by_runner_name: 'runner', choices: [{ name: 'yes', description: '', requires_checks: false, would_override_red_checks: false }] });
    expect(fromBoardDecision(wire)).toEqual({ chunk_id: 'ch_1', decision_id: 'de_1', node_name: 'approve', imposed_by_runner_id: 'rn_1', imposed_by_runner_name: 'runner', choices: ['yes'] });
  });

  it('presents absent choices as an empty list', () => {
    expect(fromBoardDecision(decisionView({ choices: undefined })).choices).toEqual([]);
  });
});

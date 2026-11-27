import type { DecisionView } from 'fleet/hub-api';
import type { BoardDecisionPresentation } from './gates.domain';

export function fromBoardDecision(decision: DecisionView): BoardDecisionPresentation {
  return {
    chunk_id: decision.chunk_id,
    decision_id: decision.decision_id,
    node_name: decision.node_name,
    imposed_by_runner_id: decision.imposed_by_runner_id,
    imposed_by_runner_name: decision.imposed_by_runner_name,
    choices: (decision.choices ?? []).map((choice) => choice.name),
  };
}

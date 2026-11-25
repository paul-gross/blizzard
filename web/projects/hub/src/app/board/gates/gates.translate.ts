import type { DecisionView } from 'fleet';
import type { BoardDecision } from './gates.domain';

export function fromBoardDecision(decision: DecisionView): BoardDecision {
  return { chunk_id: decision.chunk_id };
}

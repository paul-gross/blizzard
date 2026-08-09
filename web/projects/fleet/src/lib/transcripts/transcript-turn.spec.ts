import type { hubApi, runnerApi } from '../../public-api';
import type { TranscriptTurn } from './transcript-turn';

/**
 * The compile-time half of blizzard#248 D10: both generated turn types must stay
 * assignable to {@link TranscriptTurn} or this file fails `web:typecheck` — a future
 * divergence between the runner's and hub's independently-regenerated OpenAPI schemas
 * is caught here rather than as a runtime render bug in the shared viewer. No
 * assertions run; the check is the compiler accepting the assignment below.
 */
function assignableFromRunner(turns: runnerApi.TurnSegmentView[]): TranscriptTurn[] {
  return turns;
}

function assignableFromHub(turns: hubApi.TurnSegmentViewOutput[]): TranscriptTurn[] {
  return turns;
}

describe('TranscriptTurn assignability (blizzard#248 D10)', () => {
  it('accepts both generated turn types as a plain compile-time check', () => {
    expect(assignableFromRunner).toBeInstanceOf(Function);
    expect(assignableFromHub).toBeInstanceOf(Function);
  });
});

import { fromChunkDeleteRefusal, fromChunkWaitKind, fromMigrationMode, fromMigrationSource, fromUsageKind, fromFlowState, fromFlowFactKind, fromFlowVerdict } from './chunk-wait.translate';

describe('fromChunkWaitKind', () => {
  it('translates known waits and retains an unknown spelling', () => {
    expect(fromChunkWaitKind('pending')).toEqual({ kind: 'pending' });
    expect(fromChunkWaitKind('awaiting-merge')).toEqual({ kind: 'awaiting-merge' });
    expect(fromChunkWaitKind('future-wait')).toEqual({ kind: 'unknown', raw: 'future-wait' });
  });
});

describe('flow vocabularies', () => {
  it('preserves known and newer values for state, fact kind, and verdict', () => {
    expect(fromFlowState('polling')).toEqual({ kind: 'polling' });
    expect(fromFlowState('new-state')).toEqual({ kind: 'unknown', raw: 'new-state' });
    expect(fromFlowFactKind('turn-ended')).toEqual({ kind: 'turn-ended' });
    expect(fromFlowFactKind('new-fact')).toEqual({ kind: 'unknown', raw: 'new-fact' });
    expect(fromFlowVerdict('bounced')).toEqual({ kind: 'bounced' });
    expect(fromFlowVerdict('new-verdict')).toEqual({ kind: 'unknown', raw: 'new-verdict' });
  });
});

describe('migration and usage vocabularies', () => {
  it('keeps known cases distinct and preserves newer values', () => {
    expect(fromMigrationSource('authored-edge')).toEqual({ kind: 'authored-edge' });
    expect(fromMigrationSource('new-source')).toEqual({ kind: 'unknown', raw: 'new-source' });
    expect(fromMigrationMode('forced')).toEqual({ kind: 'forced' });
    expect(fromMigrationMode('new-mode')).toEqual({ kind: 'unknown', raw: 'new-mode' });
    expect(fromUsageKind('spawn')).toEqual({ kind: 'spawn' });
    expect(fromUsageKind('new-kind')).toEqual({ kind: 'unknown', raw: 'new-kind' });
  });
});

describe('fromChunkDeleteRefusal', () => {
  it('preserves known refusals and unrecognized reasons', () => {
    expect(fromChunkDeleteRefusal('status')).toEqual({ kind: 'status' });
    expect(fromChunkDeleteRefusal('dependents')).toEqual({ kind: 'dependents' });
    expect(fromChunkDeleteRefusal('future')).toEqual({ kind: 'unknown', raw: 'future' });
  });
});

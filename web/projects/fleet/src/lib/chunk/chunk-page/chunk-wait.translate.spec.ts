import { fromChunkDeleteRefusal, fromChunkWaitKind, fromMigrationMode, fromMigrationSource, fromUsageKind, fromFlowState, fromFlowFactKind, fromFlowVerdict, fromFlowFactView, fromChunkFlowPlaceView, fromBounceView } from './chunk-wait.translate';

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

describe('fromFlowFactView', () => {
  const wire = { at: 't', chunk_id: 'ch', flow_id: 'fl', id: 7, kind: 'landed', outcome: 'ok', revision: 'abc',
    rework: false, submission_id: 's1', verdict: 'bounced', wait: null } as never;
  it('carries every field and opens kind and verdict', () => {
    expect(fromFlowFactView(wire)).toEqual({ at: 't', chunk_id: 'ch', flow_id: 'fl', id: 7, kind: { kind: 'landed' }, outcome: 'ok',
      revision: 'abc', rework: false, submission_id: 's1', verdict: { kind: 'bounced' }, wait: null });
  });
  it('keeps an unforeseen kind or verdict and a null verdict', () => {
    const f = fromFlowFactView({ ...(wire as object), kind: 'new-kind', verdict: 'new-verdict' } as never);
    expect(f.kind).toEqual({ kind: 'unknown', raw: 'new-kind' });
    expect(f.verdict).toEqual({ kind: 'unknown', raw: 'new-verdict' });
    expect(fromFlowFactView({ ...(wire as object), verdict: null } as never).verdict).toBeNull();
  });
});

describe('fromChunkFlowPlaceView', () => {
  const wire = { branch: 'b', flow_id: 'fl', in_line: true, next_poll_at: 'n', position: 2, repository_name: 'r',
    repository_slug: 'o/r', resource: 'res', state: 'landing' } as never;
  it('carries every field and opens state', () => {
    expect(fromChunkFlowPlaceView(wire)).toEqual({ branch: 'b', flow_id: 'fl', in_line: true, next_poll_at: 'n', position: 2,
      repository_name: 'r', repository_slug: 'o/r', resource: 'res', state: { kind: 'landing' } });
    expect(fromChunkFlowPlaceView({ ...(wire as object), state: 'new-state' } as never).state).toEqual({ kind: 'unknown', raw: 'new-state' });
  });
});

describe('fromBounceView', () => {
  it('carries every field', () => {
    expect(fromBounceView({ cause: 'conflict', envelope: '{}', recorded_at: 't', rework: true, withdrawn: false } as never))
      .toEqual({ cause: 'conflict', envelope: '{}', recorded_at: 't', rework: true, withdrawn: false });
  });
});

import type { hubApi } from 'fleet';
import { chunkSummary } from 'fleet/testing';
import { fromBoardPage, fromBoardSummary, fromGraphProgressNode, fromGraphProgressState } from './chunks.translate';

describe('graph progress translation', () => {
  it('carries known node states and identifiers', () => {
    expect(fromGraphProgressNode({ node_id: 'nd_1', name: 'build', state: 'current' })).toEqual({
      nodeId: 'nd_1',
      name: 'build',
      state: { kind: 'current' },
    });
    expect(['current', 'passed', 'failed', 'unvisited'].map(
      (state) => fromGraphProgressState(state as hubApi.GraphProgressState),
    )).toEqual([
      { kind: 'current' },
      { kind: 'passed' },
      { kind: 'failed' },
      { kind: 'unvisited' },
    ]);
  });

  it('retains an unknown state', () => {
    expect(fromGraphProgressState('newer' as hubApi.GraphProgressState)).toEqual({ kind: 'unknown', raw: 'newer' });
  });
});

describe('board page metadata translation', () => {
  it('keeps the server-selected policy and cursor without exposing page chunks', () => {
    const policy = { done_since: '2026-11-01T00:00:00Z', floor: 3, window_seconds: 7200 };
    expect(fromBoardPage({ chunks: [], next_cursor: 'next', board_done_policy: policy })).toEqual({
      next_cursor: 'next',
      board_done_policy: policy,
    });
    expect(fromBoardPage({ chunks: [], next_cursor: null, board_done_policy: null }).board_done_policy).toBeNull();
  });
});

describe('board summary translation', () => {
  it('retains server judgments and translates open states', () => {
    const wire = chunkSummary({
      chunk_id: 'ch_1', graph_id: 'gr_1', status: 'future' as hubApi.ChunkStatus,
      terminal: true, promotable: false,
      graph_progress: [{ node_id: 'nd_1', name: 'build', state: 'future' as hubApi.GraphProgressState }],
    });
    const result = fromBoardSummary(wire);
    expect(result).toMatchObject({
      chunk_id: 'ch_1', status: 'future', laneStatus: { kind: 'unknown', raw: 'future' },
      terminal: true, promotable: false,
      graphProgress: [{ nodeId: 'nd_1', name: 'build', state: { kind: 'unknown', raw: 'future' } }],
    });
    expect(result).not.toHaveProperty('graph_progress');
  });
});

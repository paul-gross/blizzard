import type { hubApi } from 'fleet';
import { fromBoardPage, fromGraphProgressNode, fromGraphProgressState } from './chunks.translate';

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

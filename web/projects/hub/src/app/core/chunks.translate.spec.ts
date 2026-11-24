import type { hubApi } from 'fleet';
import { fromGraphProgressNode, fromGraphProgressState } from './chunks.translate';

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

import { dockParams, dockSelection } from './dock-selection.model';

describe('dockSelection', () => {
  it('opens the chunk `?chunk=` names, the flow `?flow=` names, or nothing', () => {
    expect(dockSelection('ch_a', null)).toEqual({ view: 'chunk', chunkId: 'ch_a' });
    expect(dockSelection(null, 'flw_1')).toEqual({ view: 'flow', flowId: 'flw_1' });
    expect(dockSelection(null, null)).toEqual({ view: 'none' });
  });

  it('lets the chunk win a hand-written URL naming both', () => {
    expect(dockSelection('ch_a', 'flw_1')).toEqual({ view: 'chunk', chunkId: 'ch_a' });
  });
});

describe('dockParams', () => {
  it('clears the other param whichever one a pick writes', () => {
    expect(dockParams({ chunk: 'ch_a' })).toEqual({ chunk: 'ch_a', flow: null });
    expect(dockParams({ flow: 'flw_1' })).toEqual({ chunk: null, flow: 'flw_1' });
    expect(dockParams({ chunk: null })).toEqual({ chunk: null, flow: null });
  });
});

import { boardLayoutOf, isBoardLayout } from './board-layout.model';

describe('board layout vocabulary', () => {
  it('maps list and tracks through and anything else to the board', () => {
    expect(boardLayoutOf('list')).toBe('list');
    expect(boardLayoutOf('tracks')).toBe('tracks');
    expect(boardLayoutOf('board')).toBe('board');
    expect(boardLayoutOf('grid')).toBe('board');
    expect(boardLayoutOf(null)).toBe('board');
  });

  it('recognises exactly the declared layouts', () => {
    for (const layout of ['board', 'list', 'tracks']) expect(isBoardLayout(layout)).toBe(true);
    for (const value of ['grid', 'LIST', '', null]) expect(isBoardLayout(value)).toBe(false);
  });
});

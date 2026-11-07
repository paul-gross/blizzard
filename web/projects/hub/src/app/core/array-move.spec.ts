import { movedItem } from './array-move';

describe('movedItem', () => {
  it('moves an item down and up', () => {
    expect(movedItem(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd']);
    expect(movedItem(['a', 'b', 'c', 'd'], 3, 1)).toEqual(['a', 'd', 'b', 'c']);
  });

  it('leaves the order alone when the indices match', () => {
    expect(movedItem(['a', 'b', 'c'], 1, 1)).toEqual(['a', 'b', 'c']);
  });

  it('returns a new array and leaves the input untouched', () => {
    const items = ['a', 'b', 'c'];
    const moved = movedItem(items, 0, 2);
    expect(moved).not.toBe(items);
    expect(items).toEqual(['a', 'b', 'c']);
  });

  it('clamps out-of-range indices into the array', () => {
    expect(movedItem(['a', 'b', 'c'], 0, 9)).toEqual(['b', 'c', 'a']);
    expect(movedItem(['a', 'b', 'c'], 2, -4)).toEqual(['c', 'a', 'b']);
    expect(movedItem(['a', 'b', 'c'], 7, 0)).toEqual(['c', 'a', 'b']);
    expect(movedItem(['a', 'b', 'c'], -1, 1)).toEqual(['b', 'a', 'c']);
  });

  it('returns an empty array for an empty input', () => {
    expect(movedItem([], 0, 3)).toEqual([]);
  });
});

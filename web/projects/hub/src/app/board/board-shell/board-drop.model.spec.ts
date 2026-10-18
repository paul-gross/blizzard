import { describe, expect, it } from 'vitest';

import { resolveDrop } from './board-drop.model';

describe('resolveDrop', () => {
  const IDS = ['a', 'b', 'c'];

  it('anchors a reorder on the chunk that ends up above it — none at the top', () => {
    expect(resolveDrop('ready', IDS, { chunkId: 'c', previousIndex: 2, currentIndex: 0, crossed: false })).toEqual({
      kind: 'reposition',
      move: { chunkId: 'c', afterChunkId: null, list: 'ready' },
    });
    expect(resolveDrop('notready', IDS, { chunkId: 'a', previousIndex: 0, currentIndex: 2, crossed: false })).toEqual({
      kind: 'reposition',
      move: { chunkId: 'a', afterChunkId: 'c', list: 'notready' },
    });
  });

  it('resolves a drop that moved nothing to nothing', () => {
    expect(resolveDrop('ready', IDS, { chunkId: 'b', previousIndex: 1, currentIndex: 1, crossed: false })).toBeNull();
  });

  it('turns a drop from the other list into a lane move, anchored in the destination as it renders', () => {
    expect(resolveDrop('ready', IDS, { chunkId: 'x', previousIndex: 4, currentIndex: 1, crossed: true })).toEqual({
      kind: 'lane',
      move: { chunkId: 'x', afterChunkId: 'a', from: 'notready', to: 'ready' },
    });
    expect(resolveDrop('notready', IDS, { chunkId: 'x', previousIndex: 0, currentIndex: 0, crossed: true })).toEqual({
      kind: 'lane',
      move: { chunkId: 'x', afterChunkId: null, from: 'ready', to: 'notready' },
    });
  });
});

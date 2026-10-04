import { type ChunkSummary } from 'fleet';
import { describe, expect, it } from 'vitest';

import {
  foldRepositions,
  gatedChunkIds,
  orderedChunkIds,
  resolveSelectedChunk,
  withPendingBoardChanges,
} from './board-page.model';

const chunk = (id: string, status: ChunkSummary['status']): ChunkSummary => ({
  chunk_id: id,
  graph_id: 'gr_1',
  status,
  current_node_id: 'nd_build',
  work_refs: [],
});

describe('gatedChunkIds', () => {
  it('collects every decision chunk id into a set', () => {
    const ids = gatedChunkIds([{ chunk_id: 'ch_a' }, { chunk_id: 'ch_b' }, { chunk_id: 'ch_a' }]);
    expect([...ids].sort()).toEqual(['ch_a', 'ch_b']);
  });

  it('is empty without decisions', () => {
    expect(gatedChunkIds([]).size).toBe(0);
  });
});

describe('orderedChunkIds', () => {
  it('keeps the hub order as bare ids', () => {
    expect(orderedChunkIds([{ chunk_id: 'ch_b' }, { chunk_id: 'ch_a' }])).toEqual(['ch_b', 'ch_a']);
  });
});

describe('withPendingBoardChanges', () => {
  const chunks = [chunk('ch_a', 'not_ready'), chunk('ch_b', 'not_ready'), chunk('ch_c', 'running')];

  it('returns the list itself with nothing pending', () => {
    expect(withPendingBoardChanges(chunks, [], [])).toBe(chunks);
  });

  it('overrides a pending promote to ready, leaving the rest', () => {
    const result = withPendingBoardChanges(chunks, [{ chunkId: 'ch_b' }], []);
    expect(result.map((c) => [c.chunk_id, c.status])).toEqual([
      ['ch_a', 'not_ready'],
      ['ch_b', 'ready'],
      ['ch_c', 'running'],
    ]);
  });

  it('drops a pending delete', () => {
    const result = withPendingBoardChanges(chunks, [], [{ chunkId: 'ch_c' }]);
    expect(result.map((c) => c.chunk_id)).toEqual(['ch_a', 'ch_b']);
  });

  it('applies a delete and a promote together', () => {
    const result = withPendingBoardChanges(chunks, [{ chunkId: 'ch_a' }], [{ chunkId: 'ch_b' }]);
    expect(result.map((c) => [c.chunk_id, c.status])).toEqual([
      ['ch_a', 'ready'],
      ['ch_c', 'running'],
    ]);
  });
});

describe('foldRepositions', () => {
  const order = ['ch_a', 'ch_b', 'ch_c'];

  it('returns the order unchanged with no moves', () => {
    expect(foldRepositions(order, [])).toEqual(order);
  });

  it('places a moved id after its anchor', () => {
    expect(foldRepositions(order, [{ chunkId: 'ch_a', afterChunkId: 'ch_c' }])).toEqual(['ch_b', 'ch_c', 'ch_a']);
  });

  it('places a moved id at the top for a null anchor', () => {
    expect(foldRepositions(order, [{ chunkId: 'ch_c', afterChunkId: null }])).toEqual(['ch_c', 'ch_a', 'ch_b']);
  });

  it('places a moved id at the top when its anchor is absent', () => {
    expect(foldRepositions(order, [{ chunkId: 'ch_b', afterChunkId: 'ch_zz' }])).toEqual(['ch_b', 'ch_a', 'ch_c']);
  });

  it('folds several moves in issue order, leaving the input untouched', () => {
    const result = foldRepositions(order, [
      { chunkId: 'ch_a', afterChunkId: 'ch_c' },
      { chunkId: 'ch_c', afterChunkId: null },
    ]);
    expect(result).toEqual(['ch_c', 'ch_b', 'ch_a']);
    expect(order).toEqual(['ch_a', 'ch_b', 'ch_c']);
  });
});

describe('resolveSelectedChunk', () => {
  const chunks = [chunk('ch_a', 'running')];

  it('is null with no selection', () => {
    expect(resolveSelectedChunk(null, chunks, 'ch_a')).toBeNull();
  });

  it('keeps a selection the board list carries', () => {
    expect(resolveSelectedChunk('ch_a', chunks, undefined)).toBe('ch_a');
  });

  it('keeps a selection only its own detail read resolved', () => {
    expect(resolveSelectedChunk('ch_old', chunks, 'ch_old')).toBe('ch_old');
  });

  it('is null for a selection neither read knows', () => {
    expect(resolveSelectedChunk('ch_gone', chunks, undefined)).toBeNull();
    expect(resolveSelectedChunk('ch_gone', chunks, 'ch_other')).toBeNull();
  });
});

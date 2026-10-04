import type { ChunkNeighborView } from '../../api/hub';
import { chunkTabOptions, openEdgeIds, preDetailState } from './chunk-page.model';

describe('chunkTabOptions', () => {
  it('offers the Transcripts tab last when the identity may read transcripts', () => {
    expect(chunkTabOptions(true).map((o) => o.value)).toEqual(['general', 'node-history', 'artifacts', 'transcripts']);
  });

  it('leaves the Transcripts tab off otherwise', () => {
    expect(chunkTabOptions(false).map((o) => o.value)).toEqual(['general', 'node-history', 'artifacts']);
  });
});

describe('preDetailState', () => {
  it('is error for a failed read and loading otherwise', () => {
    expect(preDetailState(true)).toBe('error');
    expect(preDetailState(false)).toBe('loading');
  });
});

describe('openEdgeIds', () => {
  it('names every unsatisfied edge, in order, and leaves satisfied ones off', () => {
    const edges: ChunkNeighborView[] = [
      { chunk_id: 'ch_a', satisfied: false },
      { chunk_id: 'ch_b', satisfied: true },
      { chunk_id: 'ch_c', satisfied: false },
    ];
    expect(openEdgeIds(edges)).toEqual(['ch_a', 'ch_c']);
  });

  it('is empty for no edges', () => {
    expect(openEdgeIds([])).toEqual([]);
  });
});

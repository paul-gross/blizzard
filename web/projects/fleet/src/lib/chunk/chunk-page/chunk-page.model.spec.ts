import type { ChunkNeighborView } from '../../api/hub';
import { chunkNeighborView } from '../../testing/builders/chunk';
import { chunkTabOptions, nextFocus, openEdgeIds, preDetailState } from './chunk-page.model';

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
      chunkNeighborView({ chunk_id: 'ch_a', satisfied: false }),
      chunkNeighborView({ chunk_id: 'ch_b', satisfied: true }),
      chunkNeighborView({ chunk_id: 'ch_c', satisfied: false }),
    ];
    expect(openEdgeIds(edges)).toEqual(['ch_a', 'ch_c']);
  });

  it('is empty for no edges', () => {
    expect(openEdgeIds([])).toEqual([]);
  });
});

describe('nextFocus', () => {
  it('focuses the detail back control and remembers the opened row', () => {
    expect(nextFocus('s2', null, 'data-step-key', 'node-history-back')).toEqual({
      request: { kind: 'detail', testid: 'node-history-back' },
      origin: 's2',
    });
  });

  it('returns to the row the detail was opened from', () => {
    expect(nextFocus(null, 's2', 'data-step-key', 'node-history-back')).toEqual({
      request: { kind: 'list', attribute: 'data-step-key', value: 's2' },
      origin: 's2',
    });
  });

  it('falls back to the tab when closing a detail that was deep-linked', () => {
    expect(nextFocus(null, null, 'data-step-key', 'node-history-back')).toEqual({
      request: { kind: 'tab' },
      origin: null,
    });
  });
});

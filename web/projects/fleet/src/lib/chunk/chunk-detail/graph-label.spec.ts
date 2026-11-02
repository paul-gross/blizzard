import { chunkGraphLabel } from './graph-label';

const GRAPH = { graph_id: 'gr_01KXV4DP7T7NKBE8GJJ1GXG857', graph_name: 'glacier', graph_created_at: '2026-07-19T10:00:00Z' };

describe('chunkGraphLabel', () => {
  it('reads compactRef#name-YYYYMMDD with the name and creation date present', () => {
    expect(chunkGraphLabel(GRAPH)).toBe('G-G857#glacier-20260719');
  });

  it('reads the UTC date, not the local one', () => {
    expect(chunkGraphLabel({ ...GRAPH, graph_created_at: '2026-07-19T23:59:59Z' })).toBe('G-G857#glacier-20260719');
  });

  it('degrades to the bare compact ref with no name', () => {
    expect(chunkGraphLabel({ ...GRAPH, graph_name: null })).toBe('G-G857');
  });

  it('degrades to the bare compact ref with no creation date, or an unparseable one', () => {
    expect(chunkGraphLabel({ ...GRAPH, graph_created_at: null })).toBe('G-G857');
    expect(chunkGraphLabel({ ...GRAPH, graph_created_at: 'not-a-date' })).toBe('G-G857');
  });
});

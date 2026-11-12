import { deriveActiveGraphHeader, deriveGraphHeaders } from './chunk-timeline-graphs.model';

const row = (graphId: string | null, graphName: string | null = null) => ({ graphId, graphName });
const header = (id: string, label = id) => ({ id, label });

describe('deriveGraphHeaders', () => {
  it('leaves empty and entirely graphless histories without headers', () => {
    expect(deriveGraphHeaders([])).toEqual([]);
    expect(deriveGraphHeaders([row(null), row(null)])).toEqual([null, null]);
  });

  it('keeps initial, interleaved and trailing graphless rows in place without splitting a run', () => {
    const rows = [row(null), row('gr_1', 'build'), row(null), row('gr_1', 'build'), row(null)];
    const before = rows.map((each) => ({ ...each }));
    expect(deriveGraphHeaders(rows)).toEqual([null, header('gr_1', 'build'), null, null, null]);
    expect(rows).toEqual(before);
  });

  it('distinguishes versions sharing a name and preserves repeated nonadjacent runs', () => {
    expect(deriveGraphHeaders([row('v1', 'build'), row(null), row('v2', 'build'), row('v2', 'build'), row('v1', 'build')])).toEqual([
      header('v1', 'build'), null, header('v2', 'build'), null, header('v1', 'build'),
    ]);
  });

  it('uses the id when unnamed and leaves name-only graph headers unlinked', () => {
    expect(deriveGraphHeaders([row('gr_1'), row(null, 'legacy'), row(null), row(null, 'legacy')])).toEqual([
      header('gr_1'), { id: null, label: 'legacy' }, null, null,
    ]);
  });
});

describe('deriveActiveGraphHeader', () => {
  const active = { graph_id: 'gr_active', graph_name: 'active' };

  it('starts a known active graph after empty or graphless history', () => {
    expect(deriveActiveGraphHeader([], active)).toEqual(header('gr_active', 'active'));
    expect(deriveActiveGraphHeader([row(null), row(null)], active)).toEqual(header('gr_active', 'active'));
  });

  it('ignores trailing graphless rows when continuing the active graph run', () => {
    expect(deriveActiveGraphHeader([row('gr_active', 'old label'), row(null), row(null)], active)).toBeNull();
  });

  it('starts a new active version even when graph names match', () => {
    expect(deriveActiveGraphHeader([row('gr_previous', 'active'), row(null)], active)).toEqual(header('gr_active', 'active'));
  });

  it('compares against the last known graph, not any earlier matching run', () => {
    expect(deriveActiveGraphHeader([row('gr_active'), row('gr_other'), row(null)], active)).toEqual(header('gr_active', 'active'));
  });

  it('falls back to the active graph id when it has no name', () => {
    expect(deriveActiveGraphHeader([], { graph_id: 'gr_active', graph_name: null })).toEqual(header('gr_active'));
  });
});

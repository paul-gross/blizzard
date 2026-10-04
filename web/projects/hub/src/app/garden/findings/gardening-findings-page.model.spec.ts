import { type FindingView } from 'fleet';

import { findingListRows } from './gardening-findings-page.model';

function finding(overrides: Partial<FindingView> = {}): FindingView {
  return {
    finding_id: 'fnd_1',
    class: 'stale-docstring',
    locus: 'a.py:1',
    summary: 'summary a',
    state: 'live',
    live: true,
    routine_name: 'nightly',
    scope_slug: 'blizzard',
    observed_count: 1,
    last_seen_at: '2026-01-05T00:00:00Z',
    ...overrides,
  };
}

describe('findingListRows', () => {
  it('names both routine and scope on every row of an unfiltered bucket', () => {
    expect(findingListRows([finding()], null, null)).toEqual([
      {
        findingId: 'fnd_1',
        findingClass: 'stale-docstring',
        locus: 'a.py:1',
        summary: 'summary a',
        state: 'live',
        exit: null,
        lastSeenAt: '2026-01-05T00:00:00Z',
        routineName: 'nightly',
        scopeSlug: 'blizzard',
        source: 'routine',
        severity: null,
        raisedByChunkId: null,
      },
    ]);
  });

  it('drops the routine once a routine is selected, and the scope once a scope is', () => {
    const [byRoutine] = findingListRows([finding()], 'nightly', null);
    expect(byRoutine.routineName).toBeNull();
    expect(byRoutine.scopeSlug).toBe('blizzard');
    const [byScope] = findingListRows([finding()], null, 'blizzard');
    expect(byScope.routineName).toBe('nightly');
    expect(byScope.scopeSlug).toBeNull();
  });

  it('reads a routine-less finding as a null routine name', () => {
    const [row] = findingListRows([finding({ routine_name: null })], null, null);
    expect(row.routineName).toBeNull();
  });

  it('carries exit, source, severity and raising chunk verbatim', () => {
    const [row] = findingListRows(
      [finding({ exit: 'outflow', source: 'review', severity: 'should-fix', raised_by_chunk_id: 'ch_3' })],
      'nightly',
      'blizzard',
    );
    expect(row).toMatchObject({ exit: 'outflow', source: 'review', severity: 'should-fix', raisedByChunkId: 'ch_3' });
  });

  it('maps an empty bucket to no rows', () => {
    expect(findingListRows([], null, null)).toEqual([]);
  });
});

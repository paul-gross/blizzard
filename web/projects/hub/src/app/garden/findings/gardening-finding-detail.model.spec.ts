import { type FindingDetailView } from 'fleet';

import { findingForSelection, findingPanelVm } from './gardening-finding-detail.model';

function finding(overrides: Partial<FindingDetailView> = {}): FindingDetailView {
  return {
    finding_id: 'fnd_1',
    class: 'stale-docstring',
    locus: 'a.py:1',
    summary: 'summary a',
    state: 'live',
    live: true,
    scope_slug: 'blizzard',
    observed_count: 2,
    last_seen_at: '2026-01-05T00:00:00Z',
    facts: [],
    ...overrides,
  };
}

describe('findingForSelection', () => {
  it('reads null on the bare route, whatever is cached', () => {
    expect(findingForSelection(null, finding())).toBeNull();
  });

  it('reads null while nothing is loaded', () => {
    expect(findingForSelection('fnd_1', null)).toBeNull();
  });

  it('reads null while the cached finding is a different id', () => {
    expect(findingForSelection('fnd_2', finding())).toBeNull();
  });

  it('returns the loaded finding once it is the routed one', () => {
    const loaded = finding();
    expect(findingForSelection('fnd_1', loaded)).toBe(loaded);
  });
});

describe('findingPanelVm', () => {
  const noWorkItem = (): null => null;

  it('reads null with nothing selected', () => {
    expect(findingPanelVm(null, noWorkItem)).toBeNull();
  });

  it('nulls every absent optional field and defaults the source to routine', () => {
    expect(findingPanelVm(finding(), noWorkItem)).toEqual({
      findingId: 'fnd_1',
      findingClass: 'stale-docstring',
      locus: 'a.py:1',
      state: 'live',
      exit: null,
      observedCount: 2,
      introducedRev: null,
      introducedAt: null,
      firstObservedAt: null,
      lastSeenAt: '2026-01-05T00:00:00Z',
      summary: 'summary a',
      note: null,
      facts: [],
      workItem: null,
      source: 'routine',
      severity: null,
      raisedByChunkId: null,
    });
  });

  it('carries every present field verbatim and resolves the work item by finding id', () => {
    const asked: string[] = [];
    const vm = findingPanelVm(
      finding({
        exit: 'withdrawn',
        introduced: '4ba7ef06d',
        introduced_at: '2026-01-01T00:00:00Z',
        first_observed_at: '2026-01-02T00:00:00Z',
        note: 'a note',
        source: 'review',
        severity: 'blocking',
        raised_by_chunk_id: 'ch_9',
      }),
      (findingId) => {
        asked.push(findingId);
        return { label: 'blizzard:ch_7', webUrl: null };
      },
    );
    expect(asked).toEqual(['fnd_1']);
    expect(vm).toMatchObject({
      exit: 'withdrawn',
      introducedRev: '4ba7ef06d',
      introducedAt: '2026-01-01T00:00:00Z',
      firstObservedAt: '2026-01-02T00:00:00Z',
      note: 'a note',
      workItem: { label: 'blizzard:ch_7', webUrl: null },
      source: 'review',
      severity: 'blocking',
      raisedByChunkId: 'ch_9',
    });
  });
});

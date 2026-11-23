import { fixtureInstant } from 'fleet/testing';

import type { FindingDetail } from '../core/finding.domain';
import { gardenFindingDetail } from '../core/finding.builders';
import { findingForSelection, findingPanelVm } from './gardening-finding-detail.model';

const LAST_SEEN = fixtureInstant(-3600);

function finding(overrides: Partial<FindingDetail> = {}): FindingDetail {
  return gardenFindingDetail({ locus: 'a.py:1', summary: 'summary a', observedCount: 2, lastSeenAt: LAST_SEEN, ...overrides });
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

  it('nulls every absent optional field and reads a routine-raised source', () => {
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
      lastSeenAt: LAST_SEEN,
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
        introducedRev: '4ba7ef06d',
        introducedAt: '2026-01-01T00:00:00Z',
        firstObservedAt: '2026-01-02T00:00:00Z',
        note: 'a note',
        source: 'review',
        severity: 'blocking',
        raisedByChunkId: 'ch_9',
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

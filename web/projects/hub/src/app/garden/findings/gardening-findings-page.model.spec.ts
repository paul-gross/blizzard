import { fixtureInstant } from 'fleet/testing';

import type { Finding } from '../core/finding.domain';
import { gardenFinding } from '../core/finding.builders';
import { gardenRoutine } from '../core/routine.builders';
import { findingListRows } from './gardening-findings-page.model';

const LAST_SEEN = fixtureInstant(-3600);

function finding(overrides: Partial<Finding> = {}): Finding {
  return gardenFinding({
    locus: 'a.py:1',
    summary: 'summary a',
    routineSlug: 'nightly',
    observedCount: 1,
    lastSeenAt: LAST_SEEN,
    ...overrides,
  });
}

describe('findingListRows', () => {
  it('names both routine and scope on every row of an unfiltered bucket', () => {
    expect(findingListRows([finding()], null, null, [])).toEqual([
      {
        findingId: 'fnd_1',
        findingClass: 'stale-docstring',
        locus: 'a.py:1',
        summary: 'summary a',
        state: 'live',
        exit: null,
        lastSeenAt: LAST_SEEN,
        routineName: 'nightly',
        scopeSlug: 'blizzard',
        source: 'routine',
        severity: null,
        raisedByChunkId: null,
      },
    ]);
  });

  it('drops the routine once a routine is selected, and the scope once a scope is', () => {
    const [byRoutine] = findingListRows([finding()], 'nightly', null, []);
    expect(byRoutine.routineName).toBeNull();
    expect(byRoutine.scopeSlug).toBe('blizzard');
    const [byScope] = findingListRows([finding()], null, 'blizzard', []);
    expect(byScope.routineName).toBe('nightly');
    expect(byScope.scopeSlug).toBeNull();
  });

  it("names a finding's routine by its display name, matched on the slug the finding carries", () => {
    const routines = [gardenRoutine({ slug: 'nightly', name: 'Nightly sweep' })];
    expect(findingListRows([finding()], null, null, routines)[0].routineName).toBe('Nightly sweep');
    expect(findingListRows([finding()], null, null, undefined)[0].routineName).toBe('nightly');
  });

  it('reads a routine-less finding as a null routine name', () => {
    const [row] = findingListRows([finding({ routineSlug: null })], null, null, []);
    expect(row.routineName).toBeNull();
  });

  it('carries exit, source, severity and raising chunk verbatim', () => {
    const [row] = findingListRows(
      [finding({ exit: 'outflow', source: 'review', severity: 'should-fix', raisedByChunkId: 'ch_3' })],
      'nightly',
      'blizzard',
      [],
    );
    expect(row).toMatchObject({ exit: 'outflow', source: 'review', severity: 'should-fix', raisedByChunkId: 'ch_3' });
  });

  it('maps an empty bucket to no rows', () => {
    expect(findingListRows([], null, null, [])).toEqual([]);
  });
});

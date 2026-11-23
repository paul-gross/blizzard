// The finding domain builders specs and stories build their inputs from — domain literals only, no wire, no Angular.
// The wire builders (`gardenFindingView` and kin in `fleet/testing`) stay for the translator, query, and mutation specs.
import { fixtureInstant } from 'fleet/testing';

import type { Finding, FindingDetail, FindingFact } from './finding.domain';

/** A live routine-raised finding `fnd_1` in scope `blizzard`, never observed, with no severity. */
export function gardenFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    id: 'fnd_1',
    findingClass: 'stale-docstring',
    locus: 'docs/a.md',
    summary: 'A stale docstring',
    note: null,
    state: 'live',
    exit: null,
    observedCount: 0,
    introducedRev: null,
    introducedAt: null,
    firstObservedAt: null,
    lastSeenAt: null,
    routineSlug: null,
    scopeSlug: 'blizzard',
    source: 'routine',
    severity: null,
    raisedByChunkId: null,
    ...overrides,
  };
}

/** {@link gardenFinding}'s finding with its fact chain — empty unless the caller names one. */
export function gardenFindingDetail(overrides: Partial<FindingDetail> = {}): FindingDetail {
  return { ...gardenFinding(), facts: [], ...overrides };
}

/** One `add` fact on a finding's chain, recorded by no one a week ago. */
export function gardenFindingFact(overrides: Partial<FindingFact> = {}): FindingFact {
  return {
    kind: 'add',
    note: null,
    actor: null,
    recordedAt: fixtureInstant(-7 * 24 * 3600),
    proposalId: null,
    supersededBy: null,
    ...overrides,
  };
}

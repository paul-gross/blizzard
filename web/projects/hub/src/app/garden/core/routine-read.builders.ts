// The routine-read domain builders specs and stories build their inputs from — domain literals only, no wire, no
// Angular. The wire builders (`gardenTrendView` and its kin in `fleet/testing`) stay for the translator and query specs
// and for answering a stubbed transport.
import type {
  MeasurementReading,
  ProposalCountsRow,
  RoutineTrendPeriod,
  ScopeLastSwept,
} from './routine-read.domain';

/** One quiet trend period. */
export function gardenTrendPeriod(overrides: Partial<RoutineTrendPeriod> = {}): RoutineTrendPeriod {
  return { created: 0, outflow: 0, withdrawn: 0, reopened: 0, ...overrides };
}

/** Scope `blizzard`, never swept. */
export function gardenScopeLastSwept(overrides: Partial<ScopeLastSwept> = {}): ScopeLastSwept {
  return { scopeSlug: 'blizzard', findingSetId: null, producedAt: null, revisions: {}, ...overrides };
}

/** One measurement reading on scope `blizzard`. */
export function gardenMeasurementReading(overrides: Partial<MeasurementReading> = {}): MeasurementReading {
  return { scopeSlug: 'blizzard', producedAt: '2026-01-10T00:00:00Z', measurement: '0 findings', ...overrides };
}

/** One routine-raised class `stale-docstring`, all zero. */
export function gardenProposalCountsRow(overrides: Partial<ProposalCountsRow> = {}): ProposalCountsRow {
  return {
    origin: 'routine-run',
    proposalClass: 'stale-docstring',
    created: 0,
    open: 0,
    passed: 0,
    acceptedWithItem: 0,
    acceptedWithoutItem: 0,
    ...overrides,
  };
}

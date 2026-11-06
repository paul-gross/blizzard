import type {
  FindingDetailView,
  GardenProposalAcceptResponse,
  GardenProposalClosureView,
  GardenProposalsPageView,
  GardenProposalView,
} from '../../api/hub';
import { fixtureInstant } from './clock';
import { gardenFindingDetailView } from './garden';

/** A routine-run garden proposal still waiting on a decision, under no project. */
export function gardenProposalView(
  overrides: Pick<GardenProposalView, 'proposal_id'> & Partial<GardenProposalView>,
): GardenProposalView {
  return {
    body: 'A proposal body.',
    class: 'remediate',
    closure: null,
    created_at: fixtureInstant(-24 * 3600),
    created_by: null,
    findings: [],
    origin: 'routine-run',
    project_id: null,
    project_name: null,
    project_slug: null,
    routine_name: 'comments',
    title: 'A proposal',
    ...overrides,
  };
}

/** A proposal's closure: passed by an operator with a reason, minting nothing. */
export function gardenProposalClosureView(overrides: Partial<GardenProposalClosureView> = {}): GardenProposalClosureView {
  return {
    closed_at: fixtureInstant(-3600),
    closed_by: 'u_1',
    closure: 'passed',
    item_outcome: null,
    reason: 'not worth it yet',
    ref: null,
    source: null,
    ...overrides,
  };
}

/** The accept verb's answer: the proposal, accepted and minted onto a hub work item and chunk. */
export function gardenProposalAcceptResponse(
  overrides: Pick<GardenProposalAcceptResponse, 'proposal_id'> & Partial<GardenProposalAcceptResponse>,
): GardenProposalAcceptResponse {
  return {
    ...gardenProposalView({ proposal_id: overrides.proposal_id }),
    chunk_id: 'ch_1',
    closure: gardenProposalClosureView({ closure: 'accepted', reason: null, item_outcome: 'minted', source: 'hub', ref: '42' }),
    ...overrides,
  };
}

/** One page of the docket — the last one, unless `next_cursor` says otherwise. */
export function gardenProposalsPageView(overrides: Partial<GardenProposalsPageView> = {}): GardenProposalsPageView {
  return { proposals: [], next_cursor: null, ...overrides };
}

/** A live `comments` finding a proposal cites as evidence, observed once, its locus and summary named after its id. */
export function gardenEvidenceFindingView(
  overrides: Pick<FindingDetailView, 'finding_id'> & Partial<FindingDetailView>,
): FindingDetailView {
  return gardenFindingDetailView({
    routine_name: 'comments',
    locus: `src/${overrides.finding_id}.py:1`,
    summary: `summary for ${overrides.finding_id}`,
    observed_count: 1,
    first_observed_at: fixtureInstant(-24 * 3600),
    last_seen_at: fixtureInstant(-24 * 3600),
    ...overrides,
  });
}

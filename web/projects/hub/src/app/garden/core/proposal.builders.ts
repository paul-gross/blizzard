// The garden proposal domain builders specs and stories build their inputs from — domain literals only, no wire, no
// Angular. The wire builders (`gardenProposalView` and kin in `fleet/testing`) stay for the translator, query, and
// mutation specs.
import { fixtureInstant } from 'fleet/testing';

import type { Proposal, ProposalClosure } from './proposal.domain';

/** A routine-run proposal of routine `comments` still waiting on a decision, under no project, citing no findings. */
export function gardenProposal(overrides: Pick<Proposal, 'id'> & Partial<Proposal>): Proposal {
  return {
    title: 'A proposal',
    body: 'A proposal body.',
    proposalClass: 'remediate',
    origin: 'routine-run',
    createdBy: null,
    createdAt: fixtureInstant(-24 * 3600),
    routineSlug: 'comments',
    projectId: null,
    findingIds: [],
    closure: null,
    ...overrides,
  };
}

/** A proposal's closure: passed by an operator with a reason, minting nothing. */
export function gardenProposalClosure(overrides: Partial<ProposalClosure> = {}): ProposalClosure {
  return {
    kind: 'passed',
    closedBy: 'u_1',
    closedAt: fixtureInstant(-3600),
    reason: 'not worth it yet',
    itemOutcome: null,
    source: null,
    ref: null,
    ...overrides,
  };
}

import type { RefusalMap } from 'fleet/forms';

import type { GardenProposalPassVars } from './garden-proposal.mutations';
import type { ProposalPassDraft } from './proposal-pass.domain';

/** A pass draft as the vars of the pass mutation: the trimmed reason. */
export function toPassVars(proposalId: string, draft: ProposalPassDraft): GardenProposalPassVars {
  return { proposalId, reason: draft.reason.trim() };
}

/** The hub's proposal refusals that concern the reason field. A raced closure (`garden-proposal-already-closed`),
 * an unknown proposal, and any other whole-request refusal have no field and land on the form line. */
export const PROPOSAL_PASS_REFUSALS: RefusalMap<ProposalPassDraft> = {
  field: (path) => (path === 'reason' ? 'reason' : null),
  kinds: {
    'urn:blizzard:problem:garden-proposal-invalid': 'reason',
  },
};

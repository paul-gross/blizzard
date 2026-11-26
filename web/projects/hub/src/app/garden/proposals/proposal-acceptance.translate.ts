import type { RefusalMap } from 'fleet/forms';

import type { GardenProposalAcceptVars } from './garden-proposal.mutations';
import type { ProposalAcceptanceDraft } from './proposal-acceptance.domain';

/**
 * An acceptance draft as the vars of the accept mutation. A mint sends the trimmed body and reason and the chosen
 * urgency, each only when it carries something; a decline sends only the trimmed reason, never a body or urgency left
 * over from the mint fields.
 */
export function toAcceptVars(proposalId: string, draft: ProposalAcceptanceDraft): GardenProposalAcceptVars {
  const reason = draft.reason.trim();
  if (draft.mode === 'decline') return { proposalId, mintWorkItem: false, reason };
  const body = draft.body.trim();
  return {
    proposalId,
    mintWorkItem: true,
    ...(body ? { body } : {}),
    ...(reason ? { reason } : {}),
    ...(draft.urgency ? { urgency: draft.urgency } : {}),
  };
}

const FIELD_BY_PATH: Readonly<Record<string, keyof ProposalAcceptanceDraft>> = {
  body: 'body',
  reason: 'reason',
  urgency: 'urgency',
};

/** The hub's acceptance refusals that concern one field: a body, reason or urgency path, and a blank minted body
 * (`garden-proposal-invalid`). A raced closure, an ingest conflict, a retired default graph, and any other
 * whole-request refusal have no field and land on the form line. */
export const PROPOSAL_ACCEPTANCE_REFUSALS: RefusalMap<ProposalAcceptanceDraft> = {
  field: (path) => FIELD_BY_PATH[path] ?? null,
  kinds: {
    'urn:blizzard:problem:garden-proposal-invalid': 'body',
  },
};

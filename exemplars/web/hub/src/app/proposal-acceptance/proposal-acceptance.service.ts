// exemplar: bzh:frontend-mutation-settles-on-refresh.

import { readRefusal, type SaveOutcome } from 'fleet';

import { injectAcceptProposalMutation } from './proposal-acceptance.mutations';
import { DECLINE_REASON_REQUIRED, isDeclineReasonMissing, type ProposalAcceptanceDraft } from './proposal-acceptance.domain';
import { PROPOSAL_ACCEPTANCE_REFUSALS, toAcceptVars } from './proposal-acceptance.translate';

export interface ProposalAcceptanceService {
  /** Accepts the proposal as the draft states. Resolves only after the proposal list, detail and count caches
   * refetch, and answers every refusal as a `SaveOutcome` rather than throwing it. Closure is terminal and the
   * endpoint is unversioned, so a refusal is never retried. */
  accept(proposalId: string, draft: ProposalAcceptanceDraft): Promise<SaveOutcome<ProposalAcceptanceDraft>>;
}

/** Drives the existing accept mutation. A decline with a blank reason is refused here, before any request, so the
 * rule holds without a form too. */
export function injectProposalAcceptanceService(): ProposalAcceptanceService {
  const mutation = injectAcceptProposalMutation();
  return {
    async accept(proposalId, draft) {
      if (isDeclineReasonMissing(draft)) return { ok: false, fields: { reason: DECLINE_REASON_REQUIRED }, form: null };
      try {
        await mutation.mutateAsync(toAcceptVars(proposalId, draft));
        return { ok: true };
      } catch (error) {
        return readRefusal(error, PROPOSAL_ACCEPTANCE_REFUSALS, 'Accept failed.');
      }
    },
  };
}

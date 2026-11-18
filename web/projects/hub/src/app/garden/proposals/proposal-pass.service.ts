import { readRefusal, type SaveOutcome } from 'fleet';

import { injectPassGardenProposalMutation } from './garden-proposal.mutations';
import type { ProposalPassDraft } from './proposal-pass.domain';
import { PROPOSAL_PASS_REFUSALS, toPassVars } from './proposal-pass.translate';

export interface ProposalPassService {
  /** Passes the proposal with the draft's reason. Resolves only after the proposal list, detail and count caches
   * refetch, and answers every refusal as a `SaveOutcome` rather than throwing it. Closure is terminal, so a refusal
   * is never retried. */
  pass(proposalId: string, draft: ProposalPassDraft): Promise<SaveOutcome<ProposalPassDraft>>;
}

/** Drives the existing pass mutation. */
export function injectProposalPassService(): ProposalPassService {
  const mutation = injectPassGardenProposalMutation();
  return {
    async pass(proposalId, draft) {
      try {
        await mutation.mutateAsync(toPassVars(proposalId, draft));
        return { ok: true };
      } catch (error) {
        return readRefusal(error, PROPOSAL_PASS_REFUSALS, 'Pass failed.');
      }
    },
  };
}

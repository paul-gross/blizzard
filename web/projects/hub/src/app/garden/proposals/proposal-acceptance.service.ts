import { computed, type Signal } from '@angular/core';
import { readRefusal, type SaveOutcome } from 'fleet';

import { injectAcceptGardenProposalMutation } from './garden-proposal.mutations';
import type { ProposalAcceptanceDraft } from './proposal-acceptance.domain';
import { PROPOSAL_ACCEPTANCE_REFUSALS, toAcceptVars } from './proposal-acceptance.translate';

export interface ProposalAcceptanceService {
  /** Whether an accept is in flight. */
  readonly pending: Signal<boolean>;
  /** Accepts the proposal as the draft states. Resolves only after the proposal list, detail and count caches
   * refetch, and answers every refusal as a `SaveOutcome` rather than throwing it. Closure is terminal and the
   * endpoint is unversioned, so a refusal is never retried. */
  accept(proposalId: string, draft: ProposalAcceptanceDraft): Promise<SaveOutcome<ProposalAcceptanceDraft>>;
}

/** Drives the existing accept mutation. A decline with a blank reason is refused here, before any request, so the
 * rule holds without a form too. */
export function injectProposalAcceptanceService(): ProposalAcceptanceService {
  const mutation = injectAcceptGardenProposalMutation();
  return {
    pending: computed(() => mutation.isPending()),
    async accept(proposalId, draft) {
      const vars = toAcceptVars(proposalId, draft);
      if (!vars.mintWorkItem && !vars.reason) return { ok: false, fields: { reason: 'Required' }, form: null };
      try {
        await mutation.mutateAsync(vars);
        return { ok: true };
      } catch (error) {
        return readRefusal(error, PROPOSAL_ACCEPTANCE_REFUSALS, 'Accept failed.');
      }
    },
  };
}

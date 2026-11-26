// exemplar: bzh:frontend-mutation-settles-on-refresh — the accept settles only once the reads it changed have refetched.

import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { acceptGardenProposalApiGardenProposalsProposalIdAcceptPost, type GardenProposalAcceptResponse } from 'fleet';
import { acceptGardenProposalMutationKey } from '../core/mutation-keys';
import { hubGardenProposalKey, hubGardenProposalsKey, hubRoutineProposalCountsPrefixKey } from '../core/query-keys';

/** An accept of one proposal. `reason`, `body` and `urgency` ride only when supplied, never as an explicit `null`. */
export interface ProposalAcceptVars {
  readonly proposalId: string;
  readonly mintWorkItem: boolean;
  readonly reason?: string;
  readonly body?: string;
  readonly urgency?: string;
}

/** `POST /api/garden-proposals/{proposal_id}/accept`, unversioned, so it sends no `If-Match`. Settles on the refetch of
 * the proposal list, the proposal's detail, and every cached proposal-count window. */
export function injectAcceptProposalMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: acceptGardenProposalMutationKey,
    mutationFn: async (vars: ProposalAcceptVars): Promise<GardenProposalAcceptResponse> => {
      const { data, error } = await acceptGardenProposalApiGardenProposalsProposalIdAcceptPost({
        path: { proposal_id: vars.proposalId },
        body: {
          mint_work_item: vars.mintWorkItem,
          ...(vars.reason !== undefined ? { reason: vars.reason } : {}),
          ...(vars.body !== undefined ? { body: vars.body } : {}),
          ...(vars.mintWorkItem && vars.urgency !== undefined ? { urgency: vars.urgency } : {}),
        },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubGardenProposalsKey }),
        queryClient.invalidateQueries({ queryKey: hubGardenProposalKey(vars.proposalId) }),
        queryClient.invalidateQueries({ queryKey: hubRoutineProposalCountsPrefixKey }),
      ]),
  }));
}

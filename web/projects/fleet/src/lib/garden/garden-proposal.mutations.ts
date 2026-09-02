import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  acceptGardenProposalApiGardenProposalsProposalIdAcceptPost,
  passGardenProposalApiGardenProposalsProposalIdPassPost,
  type GardenProposalAcceptResponse,
  type GardenProposalView,
} from '../api/hub';
import { hubGardenProposalKey, hubGardenProposalsKey } from '../query-keys';

/** `POST /api/garden-proposals/{proposal_id}/pass` with `{ reason }` —
 * `blizzard hub garden-proposal pass <id> --reason <text>`'s own body. */
export interface GardenProposalPassVars {
  readonly proposalId: string;
  readonly reason: string;
}

/**
 * Records that a proposal was considered and declined — the note that stops a later
 * run raising the same response as though it were new (blizzard#403). Garden
 * proposals carry no SSE event of their own (`hubGardenProposalsKey`'s own doc
 * comment), so a successful pass invalidates the docket list and this proposal's own
 * detail read directly, `scope-edit.mutations.ts`'s own shape.
 */
export function injectPassGardenProposalMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: GardenProposalPassVars): Promise<GardenProposalView> => {
      const { data, error } = await passGardenProposalApiGardenProposalsProposalIdPassPost({
        path: { proposal_id: vars.proposalId },
        body: { reason: vars.reason },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: (_data, vars) => {
      void queryClient.invalidateQueries({ queryKey: hubGardenProposalsKey });
      void queryClient.invalidateQueries({ queryKey: hubGardenProposalKey(vars.proposalId) });
    },
  }));
}

/** `POST /api/garden-proposals/{proposal_id}/accept` — `reason`/`body` ride only
 * when supplied, never as an explicit `null` (`hub/cli/garden_proposal.py`'s own
 * `accept` never sends a field the operator didn't set). */
export interface GardenProposalAcceptVars {
  readonly proposalId: string;
  readonly mintWorkItem: boolean;
  readonly reason?: string;
  readonly body?: string;
}

/**
 * Records agreement with a proposal — minting a linked hub work item by default, or
 * declining to mint when `mintWorkItem` is `false`, itself recorded rather than left
 * to read as an absent link (blizzard#403). Neither promotes the minted item nor
 * changes any finding's state — the closing route itself owns that guarantee. Same
 * direct-invalidation shape as {@link injectPassGardenProposalMutation}.
 */
export function injectAcceptGardenProposalMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: GardenProposalAcceptVars): Promise<GardenProposalAcceptResponse> => {
      const { data, error } = await acceptGardenProposalApiGardenProposalsProposalIdAcceptPost({
        path: { proposal_id: vars.proposalId },
        body: {
          mint_work_item: vars.mintWorkItem,
          ...(vars.reason !== undefined ? { reason: vars.reason } : {}),
          ...(vars.body !== undefined ? { body: vars.body } : {}),
        },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: (_data, vars) => {
      void queryClient.invalidateQueries({ queryKey: hubGardenProposalsKey });
      void queryClient.invalidateQueries({ queryKey: hubGardenProposalKey(vars.proposalId) });
    },
  }));
}

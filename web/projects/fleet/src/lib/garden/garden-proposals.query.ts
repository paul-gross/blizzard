import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  getGardenProposalApiGardenProposalsProposalIdGet,
  listGardenProposalsApiGardenProposalsGet,
  type GardenProposalView,
} from '../api/hub';
import { hubGardenProposalKey, hubGardenProposalsKey } from '../query-keys';

/**
 * Hub `GET /api/garden-proposals` read (blizzard#397) — every garden proposal, open
 * and closed alike, through TanStack Query and the generated hub client
 * (bzh:generated-client). Not yet in the SSE event vocabulary (`HUB_EVENT_TYPES`,
 * `sse/fleet-live.ts`) — like `injectHubGraphsQuery`, a plain query with no
 * `refetchInterval` is correct until a garden event exists to invalidate it.
 */
export function injectHubGardenProposalsQuery() {
  return injectQuery(() => ({
    queryKey: hubGardenProposalsKey,
    queryFn: async (): Promise<GardenProposalView[]> => {
      const { data, error } = await listGardenProposalsApiGardenProposalsGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
  }));
}

/** A proposal is still waiting on a person exactly when it carries no closure
 * (`GardenProposalView.closure`) — the gardening strip's own reading of
 * "waiting", shared here so the tab shell and any future docket sheet agree. */
export function isGardenProposalWaiting(proposal: GardenProposalView): boolean {
  return proposal.closure == null;
}

/**
 * Hub `GET /api/garden-proposals/{proposal_id}` read — one proposal's full record,
 * the docket detail's own read. Reactive over the selected proposal id,
 * `injectHubGraphQuery`'s (`graphs.query.ts`) own null-tolerant conditional-query
 * shape: disabled while nothing is selected, re-keyed and re-fetched as the
 * selection changes.
 */
export function injectHubGardenProposalQuery(proposalId: () => string | null) {
  return injectQuery(() => {
    const id = proposalId();
    return {
      queryKey: hubGardenProposalKey(id),
      enabled: id !== null,
      queryFn: async (): Promise<GardenProposalView> => {
        const { data, error } = await getGardenProposalApiGardenProposalsProposalIdGet({
          path: { proposal_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
    };
  });
}

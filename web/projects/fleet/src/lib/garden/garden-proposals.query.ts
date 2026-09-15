import { injectQuery } from '@tanstack/angular-query-experimental';

import { listGardenProposalsApiGardenProposalsGet, type GardenProposalView } from '../api/hub';
import { DRAIN_LIMIT, drainPages } from '../paginated-read';
import { hubGardenProposalsKey } from '../query-keys';

/**
 * Hub `GET /api/garden-proposals` read (blizzard#397) — every garden proposal, open
 * and closed alike, through TanStack Query and the generated hub client
 * (bzh:generated-client). Not yet in the SSE event vocabulary (`HUB_EVENT_TYPES`,
 * `sse/fleet-live.ts`) — like `injectHubGraphsQuery`, a plain query with no
 * `refetchInterval` is correct until a garden event exists to invalidate it. The
 * read is keyset-paginated on the hub (blizzard#526); {@link drainPages} follows
 * `next_cursor` to exhaustion so this query still resolves the whole list.
 */
export function injectHubGardenProposalsQuery() {
  return injectQuery(() => ({
    queryKey: hubGardenProposalsKey,
    queryFn: (): Promise<GardenProposalView[]> =>
      drainPages(
        (cursor) =>
          listGardenProposalsApiGardenProposalsGet({ query: { cursor, limit: DRAIN_LIMIT }, throwOnError: false }),
        (page) => page.proposals,
      ),
  }));
}

/** A proposal is still waiting on a person exactly when it carries no closure
 * (`GardenProposalView.closure`) — the gardening strip's own reading of
 * "waiting", shared here so the tab shell and any future docket sheet agree. */
export function isGardenProposalWaiting(proposal: GardenProposalView): boolean {
  return proposal.closure == null;
}

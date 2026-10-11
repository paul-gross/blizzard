import { injectQuery } from '@tanstack/angular-query-experimental';

import { injectProjectFilter, lensedKey } from 'fleet/shell';
import { listGardenProposalsApiGardenProposalsGet, listProjectGardenProposalsApiProjectsProjectGardenProposalsGet, type GardenProposalView, hubGardenProposalsKey } from 'fleet';
import { DRAIN_LIMIT, drainPages } from '../../core/paginated-read';

/**
 * The docket read — every garden proposal, open and closed alike: the lensed project's
 * (`GET /api/projects/{project}/garden-proposals`), or on All the tenant's (`GET /api/garden-proposals`), which only
 * the gardening doorway reads, through TanStack Query and the generated hub client
 * (bzh:generated-client). Not yet in the SSE event vocabulary (`HUB_EVENT_TYPES`,
 * `sse/fleet-live.ts`) — like `injectHubGraphsQuery`, a plain query with no
 * `refetchInterval` is correct until a garden event exists to invalidate it. The
 * read is keyset-paginated on the hub; {@link drainPages} follows
 * `next_cursor` to exhaustion so this query still resolves the whole list.
 *
 * `tenantWide` reads the whole tenant even under a lens, for a figure that sets one project beside the others.
 */
export function injectHubGardenProposalsQuery(options: { readonly tenantWide?: boolean } = {}) {
  const filter = injectProjectFilter();
  const project = options.tenantWide === true ? () => null : filter;
  return injectQuery(() => {
    const lens = project();
    return {
      queryKey: lensedKey(hubGardenProposalsKey, lens),
      queryFn: (): Promise<GardenProposalView[]> =>
        drainPages(
          (cursor) =>
            lens === null
              ? listGardenProposalsApiGardenProposalsGet({ query: { cursor, limit: DRAIN_LIMIT }, throwOnError: false })
              : listProjectGardenProposalsApiProjectsProjectGardenProposalsGet({
                  path: { project: lens },
                  query: { cursor, limit: DRAIN_LIMIT },
                  throwOnError: false,
                }),
          (page) => page.proposals,
        ),
    };
  });
}

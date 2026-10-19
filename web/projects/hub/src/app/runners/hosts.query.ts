import { injectQuery } from '@tanstack/angular-query-experimental';

import { type HostRegistryView, listHostsApiHostsGet, LIVE_COVERED_POLL_BACKSTOP_MS, hubHostsKey } from 'fleet/shell';

/**
 * Hub `GET /api/hosts` read — the host registry the runners view groups its runners under,
 * each host with its reported workspaces and subscription usage, through TanStack Query and
 * the generated hub client (bzh:generated-client).
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 *
 * Follows the runners read's retired flag: retired hosts are excluded unless `includeRetired()`
 * is true. The include-retired read keys under {@link hubHostsKey}, so an invalidation of that
 * key re-reads both.
 */
export function injectHubHostsQuery(includeRetired: () => boolean = () => false) {
  return injectQuery(() => ({
    queryKey: includeRetired() ? [...hubHostsKey, 'include-retired'] : hubHostsKey,
    queryFn: async (): Promise<HostRegistryView[]> => {
      const { data, error } = await listHostsApiHostsGet({
        query: includeRetired() ? { include_retired: true } : undefined,
        throwOnError: false,
      });
      if (error) throw error;
      return data?.hosts ?? [];
    },
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

import { injectQuery } from '@tanstack/angular-query-experimental';

import { type HostRegistryView, listHostsApiHostsGet, LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet/shell';
import { hostFact } from './host.translate';
import { hubHostsKey } from './query-keys';

/**
 * Hub `GET /api/hosts` read — the shared host registry,
 * each host with its reported workspaces and subscription usage, through TanStack Query and
 * the generated hub client (bzh:generated-client).
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/hub/src/app/core/live/hub-live-updates.ts`).
 *
 * Follows the runners read's retired flag: retired hosts are excluded unless `includeRetired()`
 * is true. The include-retired read keys under {@link hubHostsKey}, so an invalidation of that
 * key re-reads both.
 */
export function hostQueryOptions(includeRetired: () => boolean) {
  const retired = includeRetired();
  return {
    queryKey: retired ? [...hubHostsKey, 'include-retired'] : hubHostsKey,
    queryFn: async (): Promise<HostRegistryView[]> => {
      const { data, error } = await listHostsApiHostsGet({
        query: retired ? { include_retired: true } : undefined,
        throwOnError: false,
      });
      if (error) throw error;
      return data?.hosts ?? [];
    },
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  };
}

/** Domain projection over the same cached registry read. */
export function injectHostFactsQuery(includeRetired: () => boolean = () => false) {
  return injectQuery(() => ({ ...hostQueryOptions(includeRetired), select: (rows: HostRegistryView[]) => rows.map(hostFact) }));
}

import { injectQuery } from '@tanstack/angular-query-experimental';

import { type RunnerRegistryView, listRunnersApiRunnersGet, LIVE_COVERED_POLL_BACKSTOP_MS, hubRunnersKey } from 'fleet';

/**
 * Hub `GET /api/runners` read — the fleet registry with each runner's derived
 * liveness (`online` vs the 5-min staleness threshold) and `paused` state,
 * through TanStack Query and the generated hub client (bzh:generated-client).
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 *
 * Retired runners are excluded unless `includeRetired()` is true. The include-retired read keys
 * under {@link hubRunnersKey}, so an invalidation of that key re-reads both.
 */
export function injectHubRunnersQuery(includeRetired: () => boolean = () => false) {
  return injectQuery(() => ({
    queryKey: includeRetired() ? [...hubRunnersKey, 'include-retired'] : hubRunnersKey,
    queryFn: async (): Promise<RunnerRegistryView[]> => {
      const { data, error } = await listRunnersApiRunnersGet({
        query: includeRetired() ? { include_retired: true } : undefined,
        throwOnError: false,
      });
      if (error) throw error;
      return data?.runners ?? [];
    },
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

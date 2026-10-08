import { injectQuery } from '@tanstack/angular-query-experimental';

import { type DecisionView, listDecisionsApiDecisionsGet, LIVE_COVERED_POLL_BACKSTOP_MS, hubDecisionsKey } from 'fleet/shell';

/**
 * Hub `GET /api/decisions` read — every open (unresolved) gate across the fleet,
 * through TanStack Query and the generated hub client (bzh:generated-client). Every
 * reader shares this one cache entry by key.
 *
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 */
export function injectHubDecisionsQuery() {
  return injectQuery(() => ({
    queryKey: hubDecisionsKey,
    queryFn: async (): Promise<DecisionView[]> => {
      const { data, error } = await listDecisionsApiDecisionsGet({ throwOnError: false });
      if (error) throw error;
      return data?.decisions ?? [];
    },
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

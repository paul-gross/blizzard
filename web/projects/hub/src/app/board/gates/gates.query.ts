import { injectQuery } from '@tanstack/angular-query-experimental';

import { type DecisionView, listDecisionsApiDecisionsGet, LIVE_COVERED_POLL_BACKSTOP_MS, hubDecisionsKey } from 'fleet/shell';

/**
 * Hub `GET /api/decisions` read — every open (unresolved) gate across the fleet,
 * through TanStack Query and the generated hub client (bzh:generated-client). The
 * right rail lists it, the board cards are flagged from it, and the mobile glance
 * folds it into "Needs you"; all three share this one cache entry by key.
 *
 * The live-update service re-reads this on `decision-opened` / `decision-resolved`;
 * the poll is a backstop, not the primary freshness path.
 */
export function injectHubDecisionsQuery() {
  return injectQuery(() => ({
    queryKey: hubDecisionsKey,
    queryFn: async (): Promise<DecisionView[]> => {
      const { data, error } = await listDecisionsApiDecisionsGet({ throwOnError: false });
      if (error) throw error;
      return data?.decisions ?? [];
    },
    // Covered by decision-opened/decision-resolved (EVENT_INVALIDATION_REGISTRY,
    // sse/fleet-live.ts). See LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

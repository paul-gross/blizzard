import { injectQuery } from '@tanstack/angular-query-experimental';

import { type FlowView, hubFlowsKey, listFlowsApiFlowsGet } from 'fleet';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet/shell';

/**
 * `GET /api/flows?active=true` — the tenant's active flows, each with its line. Tenant-wide and never lensed, so it
 * sends no `?project=`: a branch's line is shared by every project landing on it, and a lensed chunk still shows whom
 * it waits behind. Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 */
export function injectActiveFlowsQuery() {
  return injectQuery(() => ({
    queryKey: [...hubFlowsKey, 'active'] as const,
    queryFn: async (): Promise<FlowView[]> => {
      const { data, error } = await listFlowsApiFlowsGet({
        query: { active: true },
        throwOnError: false,
      });
      if (error) throw error;
      return data?.flows ?? [];
    },
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

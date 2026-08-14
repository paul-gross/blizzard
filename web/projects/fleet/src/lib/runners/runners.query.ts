import { injectQuery } from '@tanstack/angular-query-experimental';

import { type RunnerView, listRunnersApiRunnersGet } from '../api/hub';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from '../polling';
import { hubRunnersKey } from '../query-keys';

/**
 * Hub `GET /api/runners` read — the fleet registry with each runner's derived
 * liveness (`online` vs the 5-min staleness threshold) and `paused` state,
 * through TanStack Query and the generated hub client (bzh:generated-client).
 * The live-update service re-reads this on `runner-changed`; the poll is a backstop
 * (issue #316), not the primary freshness path.
 */
export function injectHubRunnersQuery() {
  return injectQuery(() => ({
    queryKey: hubRunnersKey,
    queryFn: async (): Promise<RunnerView[]> => {
      const { data, error } = await listRunnersApiRunnersGet({ throwOnError: false });
      if (error) throw error;
      return data?.runners ?? [];
    },
    // Covered by runner-changed (EVENT_INVALIDATION_REGISTRY, sse/fleet-live.ts).
    // See LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

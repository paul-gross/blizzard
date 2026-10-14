import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  type DecisionView,
  listDecisionsApiDecisionsGet,
  LIVE_COVERED_POLL_BACKSTOP_MS,
  hubDecisionsKey,
  injectProjectFilter,
  lensedKey,
} from 'fleet/shell';

/**
 * Hub `GET /api/decisions` read — every open (unresolved) gate across the fleet,
 * through TanStack Query and the generated hub client (bzh:generated-client). Every
 * reader shares this one cache entry by key.
 *
 * Under a project lens the read carries `?project=`, so the hub narrows the list to the lensed project's gates
 * whatever the board's chunk list holds. The key carries the lens.
 *
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 */
export function injectHubDecisionsQuery() {
  const projectFilter = injectProjectFilter();
  return injectQuery(() => {
    const project = projectFilter();
    return {
      queryKey: lensedKey(hubDecisionsKey, project),
      queryFn: async (): Promise<DecisionView[]> => {
        const { data, error } = await listDecisionsApiDecisionsGet({
          query: { project: project ?? undefined },
          throwOnError: false,
        });
        if (error) throw error;
        return data?.decisions ?? [];
      },
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}

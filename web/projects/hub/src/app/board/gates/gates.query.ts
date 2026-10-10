import { inject } from '@angular/core';
import { QueryClient, injectQuery } from '@tanstack/angular-query-experimental';

import {
  type DecisionView,
  listDecisionsApiDecisionsGet,
  LIVE_COVERED_POLL_BACKSTOP_MS,
  hubDecisionsKey,
  injectProjectFilter,
  lensedKey,
} from 'fleet/shell';
import { inProjectChunks } from '../../core/chunks.query';

/**
 * Hub `GET /api/decisions` read — every open (unresolved) gate across the fleet,
 * through TanStack Query and the generated hub client (bzh:generated-client). Every
 * reader shares this one cache entry by key.
 *
 * Under a project lens the list keeps only the gates on the project's chunks ({@link inProjectChunks}): the hub reads
 * this list whole, and a gate belongs to its chunk's project. The key carries the lens.
 *
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 */
export function injectHubDecisionsQuery() {
  const queryClient = inject(QueryClient);
  const projectFilter = injectProjectFilter();
  return injectQuery(() => {
    const project = projectFilter();
    return {
      queryKey: lensedKey(hubDecisionsKey, project),
      queryFn: async (): Promise<DecisionView[]> => {
        const { data, error } = await listDecisionsApiDecisionsGet({ throwOnError: false });
        if (error) throw error;
        return inProjectChunks(queryClient, project, data?.decisions ?? []);
      },
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}

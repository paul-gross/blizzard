import { inject } from '@angular/core';
import { QueryClient, injectQuery } from '@tanstack/angular-query-experimental';

import {
  type QuestionView,
  listOpenQuestionsApiQuestionsGet,
  LIVE_COVERED_POLL_BACKSTOP_MS,
  hubQuestionsKey,
  injectProjectFilter,
  lensedKey,
} from 'fleet/shell';
import { inProjectChunks } from '../../core/chunks.query';

/**
 * Hub `GET /api/questions` read — every open (unanswered) question across the
 * fleet, through TanStack Query and the generated hub
 * client (bzh:generated-client). This is the fleet-wide ask list, distinct from a
 * single chunk's `questions` in its detail aggregate.
 *
 * Under a project lens the list keeps only the questions on the project's chunks ({@link inProjectChunks}): the hub
 * reads this list whole, and a question belongs to its chunk's project. The key carries the lens.
 *
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 */
export function injectHubQuestionsQuery() {
  const queryClient = inject(QueryClient);
  const projectFilter = injectProjectFilter();
  return injectQuery(() => {
    const project = projectFilter();
    return {
      queryKey: lensedKey(hubQuestionsKey, project),
      queryFn: async (): Promise<QuestionView[]> => {
        const { data, error } = await listOpenQuestionsApiQuestionsGet({ throwOnError: false });
        if (error) throw error;
        return inProjectChunks(queryClient, project, data ?? []);
      },
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}

import { injectQuery } from '@tanstack/angular-query-experimental';

import { type QuestionView, listOpenQuestionsApiQuestionsGet, LIVE_COVERED_POLL_BACKSTOP_MS, hubQuestionsKey } from 'fleet/shell';

/**
 * Hub `GET /api/questions` read — every open (unanswered) question across the
 * fleet, through TanStack Query and the generated hub
 * client (bzh:generated-client). This is the fleet-wide ask list, distinct from a
 * single chunk's `questions` in its detail aggregate.
 *
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 */
export function injectHubQuestionsQuery() {
  return injectQuery(() => ({
    queryKey: hubQuestionsKey,
    queryFn: async (): Promise<QuestionView[]> => {
      const { data, error } = await listOpenQuestionsApiQuestionsGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

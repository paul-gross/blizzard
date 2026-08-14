import { injectQuery } from '@tanstack/angular-query-experimental';

import { type QuestionView, listOpenQuestionsApiQuestionsGet } from '../api/hub';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from '../polling';
import { hubQuestionsKey } from '../query-keys';

/**
 * Hub `GET /api/questions` read — every open (unanswered) question across the
 * fleet (MVP criterion 7), through TanStack Query and the generated hub
 * client (bzh:generated-client). This is the fleet-wide ask list the right rail
 * shows, distinct from a single chunk's `questions` in its detail aggregate: the
 * rail must surface an ask on a chunk nobody has selected.
 *
 * The live-update service re-reads this on `question-asked` / `question-answered`;
 * the poll is a backstop (issue #316), not the primary freshness path.
 */
export function injectHubQuestionsQuery() {
  return injectQuery(() => ({
    queryKey: hubQuestionsKey,
    queryFn: async (): Promise<QuestionView[]> => {
      const { data, error } = await listOpenQuestionsApiQuestionsGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
    // Covered by question-asked/question-answered (EVENT_INVALIDATION_REGISTRY,
    // sse/fleet-live.ts). See LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

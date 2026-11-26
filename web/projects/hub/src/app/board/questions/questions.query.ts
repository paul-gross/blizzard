import { injectQuery } from '@tanstack/angular-query-experimental';

import { type QuestionView, listOpenQuestionsApiQuestionsGet, LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet/shell';
import { hubQuestionsKey, lensedKey } from '../../core/query-keys';
import { injectProjectFilter } from '../../core/project-filter';
import { fromBoardQuestion } from './questions.translate';

/**
 * Hub `GET /api/questions` read — every open (unanswered) question across the
 * fleet, through TanStack Query and the generated hub
 * client (bzh:generated-client). This is the fleet-wide ask list, distinct from a
 * single chunk's `questions` in its detail aggregate.
 *
 * Under a project lens the read carries `?project=`, so the hub narrows the list to the lensed project's questions
 * whatever the board's chunk list holds. The key carries the lens.
 *
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/hub/src/app/core/live/hub-live-updates.ts`).
 */
function questionsQueryOptions(project: string | null) {
  return {
      queryKey: lensedKey(hubQuestionsKey, project),
      queryFn: async (): Promise<QuestionView[]> => {
        const { data, error } = await listOpenQuestionsApiQuestionsGet({
          query: { project: project ?? undefined },
          throwOnError: false,
        });
        if (error) throw error;
        return data ?? [];
      },
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  };
}

export function injectBoardQuestionsQuery() {
  const projectFilter = injectProjectFilter();
  return injectQuery(() => ({
    ...questionsQueryOptions(projectFilter()),
    select: (questions: QuestionView[]) => questions.map(fromBoardQuestion),
  }));
}

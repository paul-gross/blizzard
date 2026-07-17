import { injectQuery } from '@tanstack/angular-query-experimental';

import { type QuestionView, listOpenQuestionsApiQuestionsGet } from '../api/hub';
import { hubQuestionsKey } from '../query-keys';

/**
 * Hub `GET /api/questions` read — every open (unanswered) question across the
 * fleet (D-052, MVP criterion 7), through TanStack Query and the generated hub
 * client (bzh:generated-client). This is the fleet-wide ask list the right rail
 * shows, distinct from a single chunk's `questions` in its detail aggregate: the
 * rail must surface an ask on a chunk nobody has selected.
 *
 * The live-update service re-reads this on `question-asked` / `question-answered`;
 * the poll is the floor.
 */
export function injectHubQuestionsQuery() {
  return injectQuery(() => ({
    queryKey: hubQuestionsKey,
    queryFn: async (): Promise<QuestionView[]> => {
      const { data, error } = await listOpenQuestionsApiQuestionsGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
    refetchInterval: 5000,
  }));
}

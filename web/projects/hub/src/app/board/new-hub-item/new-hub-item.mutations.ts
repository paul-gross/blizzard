import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { createWorkItemApiWorkSourcesSourceItemsPost, hubBacklogKey, hubChunksKey } from 'fleet';
import { createHubItemMutationKey } from '../../core/mutation-keys';

/**
 * `POST /api/work-sources/hub/items` — creates a hub item in a project at an urgency, through the generated client
 * (bzh:generated-client), resolving to the minted chunk's id. On settle it re-reads the chunk list and the backlog the
 * new card joins, and stays pending until both reads land.
 */
export function injectCreateHubItemMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: createHubItemMutationKey,
    mutationFn: async (vars: {
      readonly project: string;
      readonly title: string;
      readonly body: string;
      readonly urgency: string;
    }): Promise<string> => {
      const { data, error } = await createWorkItemApiWorkSourcesSourceItemsPost({
        path: { source: 'hub' },
        query: { project: vars.project },
        body: { title: vars.title, body: vars.body, urgency: vars.urgency },
        throwOnError: false,
      });
      if (error) throw error;
      return data!.chunk_id;
    },
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubChunksKey }),
        queryClient.invalidateQueries({ queryKey: hubBacklogKey }),
      ]),
  }));
}

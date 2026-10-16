import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { demoteChunkApiChunksChunkIdDemotePost, hubBacklogKey, hubChunkKey, hubChunksKey, hubQueueKey } from 'fleet';
import { demoteChunkMutationKey, laneMoveScopeId } from '../../core/mutation-keys';
import { placementBody } from './promote.mutations';

/**
 * Demote a ready chunk back to not-ready — the board's counterpart of `blizzard hub chunk demote`.
 * `afterChunkId` places it: a backlog chunk to land right after, `null` for the head of the backlog,
 * absent for the tail.
 */
export interface DemoteVars {
  readonly chunkId: string;
  readonly afterChunkId?: string | null;
}

/**
 * `POST /api/chunks/{id}/demote` — return an unclaimed ready chunk to the backlog, through the
 * generated client (bzh:generated-client). Idempotent server-side. On settle it re-reads the fleet
 * list, the ready queue, and the backlog (the chunk leaves the queue and joins the backlog), plus
 * the chunk detail. It shares the lane-move scope, so it runs behind any move still pending.
 */
export function injectDemoteChunkMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: demoteChunkMutationKey,
    scope: { id: laneMoveScopeId },
    mutationFn: async (vars: DemoteVars): Promise<void> => {
      const { error } = await demoteChunkApiChunksChunkIdDemotePost({
        path: { chunk_id: vars.chunkId },
        body: placementBody(vars.afterChunkId),
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubChunksKey }),
        queryClient.invalidateQueries({ queryKey: hubQueueKey }),
        queryClient.invalidateQueries({ queryKey: hubBacklogKey }),
        queryClient.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId) }),
      ]),
  }));
}

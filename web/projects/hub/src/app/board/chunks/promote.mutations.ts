import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { promoteChunkApiChunksChunkIdPromotePost, hubBacklogKey, hubChunkKey, hubChunksKey, hubQueueKey } from 'fleet';
import { laneMoveScopeId, promoteChunkMutationKey } from '../../core/mutation-keys';

/**
 * Promote a not-ready chunk to ready — the board's counterpart of `blizzard hub promote`. `afterChunkId`
 * places it: a ready chunk to land right after, `null` for the head of the queue, absent for the tail.
 */
export interface PromoteVars {
  readonly chunkId: string;
  readonly afterChunkId?: string | null;
}

/** The request body for a placement: nothing for the tail, `to_top` for the head, else the anchor. */
export function placementBody(afterChunkId: string | null | undefined) {
  if (afterChunkId === undefined) return undefined;
  return afterChunkId === null ? { to_top: true } : { after_chunk_id: afterChunkId };
}

/**
 * `POST /api/chunks/{id}/promote` — flip a chunk out of its not-ready resting state so a
 * runner may claim it, through the generated client (bzh:generated-client).
 * Idempotent server-side. On success it re-reads the fleet list, the ready queue, and
 * the backlog (the promoted chunk leaves the backlog and joins the queue), plus the
 * chunk detail. It shares the lane-move scope, so an anchored promote runs behind any
 * move still pending that it may anchor on.
 */
export function injectPromoteChunkMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: promoteChunkMutationKey,
    scope: { id: laneMoveScopeId },
    mutationFn: async (vars: PromoteVars): Promise<void> => {
      const { error } = await promoteChunkApiChunksChunkIdPromotePost({
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

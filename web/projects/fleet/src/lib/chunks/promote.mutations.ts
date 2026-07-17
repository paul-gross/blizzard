import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { promoteChunkApiChunksChunkIdPromotePost } from '../api/hub';
import { hubChunkKey, hubChunksKey, hubQueueKey } from '../query-keys';

/** Promote a not-ready chunk to ready — the board's counterpart of `blizzard hub promote`. */
export interface PromoteVars {
  readonly chunkId: string;
}

/**
 * `POST /api/chunks/{id}/promote` — flip a chunk out of its not-ready resting state so a
 * runner may claim it, through the generated client (bzh:generated-client).
 * Idempotent server-side. On success it re-reads the fleet list and the ready queue (the
 * promoted chunk leaves the not-ready column and joins the queue), plus the chunk detail.
 */
export function injectPromoteChunkMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: PromoteVars): Promise<void> => {
      const { error } = await promoteChunkApiChunksChunkIdPromotePost({
        path: { chunk_id: vars.chunkId },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSuccess: (_data, vars) => {
      void queryClient.invalidateQueries({ queryKey: hubChunksKey });
      void queryClient.invalidateQueries({ queryKey: hubQueueKey });
      void queryClient.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId) });
    },
  }));
}

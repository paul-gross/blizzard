import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { pauseChunkApiChunksChunkIdPausePost, resumeChunkApiChunksChunkIdResumePost, hubChunkKey, hubChunksKey, hubQueueKey } from 'fleet';
import { chunkPauseMutationKey } from '../../core/mutation-keys';

/** The chunk whose operator pause brake to set, and the desired state. */
export interface ChunkPauseVars {
  readonly chunkId: string;
  readonly paused: boolean;
}

/**
 * `POST /api/chunks/{id}/pause|resume`, chosen by the desired `paused` state, through
 * the generated client (bzh:generated-client). A refusal throws; it settles by
 * re-reading the fleet list, the ready queue, and the chunk detail.
 */
export function injectChunkPauseMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: chunkPauseMutationKey,
    mutationFn: async (vars: ChunkPauseVars): Promise<void> => {
      const call = vars.paused ? pauseChunkApiChunksChunkIdPausePost : resumeChunkApiChunksChunkIdResumePost;
      const { error } = await call({
        path: { chunk_id: vars.chunkId },
        body: { by: 'operator' },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubChunksKey }),
        queryClient.invalidateQueries({ queryKey: hubQueueKey }),
        queryClient.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId) }),
      ]),
  }));
}

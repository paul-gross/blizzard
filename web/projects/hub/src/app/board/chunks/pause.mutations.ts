import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { pauseChunkApiChunksChunkIdPausePost, resumeChunkApiChunksChunkIdResumePost, hubChunkKey, hubChunksKey, hubQueueKey } from 'fleet';
import { chunkPauseMutationKey } from '../../core/mutation-keys';

/** Toggle a chunk's operator pause brake: pausing holds the claim, kills
 * the active worker, and takes it off the ready queue; resuming clears the brake. */
export interface ChunkPauseVars {
  readonly chunkId: string;
  readonly paused: boolean;
}

/**
 * `POST /api/chunks/{id}/pause|resume` — routed to the pause or resume verb by the
 * desired `paused` state, through the generated client (bzh:generated-client).
 * Server-refused for `{done, stopped, delivering}` (`PauseService`) — a refusal
 * reaches the caller as a thrown error, nothing here swallows it. On success it
 * re-reads the fleet list, the ready queue, and the chunk detail. `by` defaults to `operator` server-side.
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

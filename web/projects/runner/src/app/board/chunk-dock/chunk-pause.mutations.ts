import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { chunkPauseMutationKey } from '../../core/mutation-keys';
import { runnerChunkDetailKey, runnerLeasesKey } from '../../core/query-keys';

/** Toggle a chunk's operator pause brake from the machine panel: `paused` is the
 * state to set — `true` pauses the chunk, `false` resumes it. */
export interface ChunkPauseVars {
  readonly chunkId: string;
  readonly paused: boolean;
}

/**
 * `POST /api/chunks/{id}/pause|resume` — the runner's pass-through proxy onto the hub's
 * fleet-mounted counterpart, called through the generated `pauseChunkApiChunksChunkIdPausePost`
 * or `resumeChunkApiChunksChunkIdResumePost` by the desired `paused` state
 * (bzh:generated-client). The hub refuses a chunk whose `ChunkDetail.pausable` is false — the
 * header offers the toggle only while the wire's `pausable` holds, and surfaces a 409 anyway if
 * the race is lost. On success it re-reads the chunk's detail (the pause fact the header renders off)
 * and the leases list (the derived machine status the row/dock summary render off).
 */
export function injectChunkPauseMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: chunkPauseMutationKey,
    mutationFn: async (vars: ChunkPauseVars): Promise<void> => {
      const call = vars.paused
        ? runnerApi.pauseChunkApiChunksChunkIdPausePost
        : runnerApi.resumeChunkApiChunksChunkIdResumePost;
      const { error } = await call({ path: { chunk_id: vars.chunkId }, throwOnError: false });
      if (error) throw error;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: runnerChunkDetailKey(vars.chunkId) }),
        queryClient.invalidateQueries({ queryKey: runnerLeasesKey }),
      ]),
  }));
}

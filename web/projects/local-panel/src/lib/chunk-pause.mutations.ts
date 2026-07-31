import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { runnerChunkDetailKey, runnerLeasesKey } from './query-keys';

/** Toggle a chunk's operator pause brake from the machine panel (issue #185): pausing
 * holds the claim, kills the active worker, and takes it off the ready queue; resuming
 * clears the brake — mirrors the hub board's `injectChunkPauseMutation`. */
export interface ChunkPauseVars {
  readonly chunkId: string;
  readonly paused: boolean;
}

/**
 * `POST /api/chunks/{id}/pause|resume` — the runner's pass-through proxy onto the hub's
 * fleet-mounted counterpart (`blizzard.runner.api.chunk_detail`), routed to the pause or
 * resume verb by the desired `paused` state through the generated client
 * (bzh:generated-client). Server-refused for `{done, stopped, delivering}` — the header
 * mirrors that refusal so it never offers a 409, and surfaces one anyway if the race is
 * lost. On success it re-reads the chunk's detail (the pause fact the header renders off)
 * and the leases list (the derived machine status the row/dock summary render off).
 */
export function injectChunkPauseMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: ChunkPauseVars): Promise<void> => {
      const call = vars.paused
        ? runnerApi.pauseChunkApiChunksChunkIdPausePost
        : runnerApi.resumeChunkApiChunksChunkIdResumePost;
      const { error } = await call({ path: { chunk_id: vars.chunkId }, throwOnError: false });
      if (error) throw error;
    },
    onSuccess: (_data, vars) => {
      void queryClient.invalidateQueries({ queryKey: runnerChunkDetailKey(vars.chunkId) });
      void queryClient.invalidateQueries({ queryKey: runnerLeasesKey });
    },
  }));
}

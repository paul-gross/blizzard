import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { pauseChunkApiChunksChunkIdPausePost, resumeChunkApiChunksChunkIdResumePost } from '../api/hub';
import { hubChunkKey, hubChunksKey, hubQueueKey } from '../query-keys';

/** Toggle a chunk's operator pause brake (issue #46): pausing holds the claim, kills
 * the active worker, and takes it off the ready queue; resuming clears the brake. */
export interface ChunkPauseVars {
  readonly chunkId: string;
  readonly paused: boolean;
}

/**
 * `POST /api/chunks/{id}/pause|resume` — routed to the pause or resume verb by the
 * desired `paused` state (mirrors `injectRunnerPauseMutation`), through the generated
 * client (bzh:generated-client). Server-refused for `{done, stopped, delivering}`
 * (`PauseService`); the chunk detail dock mirrors that refusal so it never offers a
 * 409, and surfaces one anyway if the race is lost — a refusal reaches the caller as a
 * thrown error, nothing here swallows it (issue #42's pattern). On success it re-reads
 * the fleet list, the ready queue, and the chunk detail — the same three keys
 * `injectPromoteChunkMutation` invalidates. `by` defaults to `operator` server-side.
 */
export function injectChunkPauseMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: ChunkPauseVars): Promise<void> => {
      const call = vars.paused ? pauseChunkApiChunksChunkIdPausePost : resumeChunkApiChunksChunkIdResumePost;
      const { error } = await call({
        path: { chunk_id: vars.chunkId },
        body: { by: 'operator' },
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

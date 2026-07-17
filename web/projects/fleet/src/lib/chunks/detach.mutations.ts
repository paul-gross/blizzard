import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { detachChunkApiChunksChunkIdDetachPost } from '../api/hub';
import { hubChunkKey, hubChunksKey, hubQueueKey } from '../query-keys';

/** Forcibly detach a chunk from its runner — the board's counterpart of
 * `blizzard hub detach` (D-088). Not requeue: it writes no supersession fact and
 * bumps no epoch, so a `needs_human` chunk detached this way still derives
 * `needs_human` (`src/blizzard/hub/domain/detach.py`). */
export interface DetachVars {
  readonly chunkId: string;
}

/**
 * `POST /api/chunks/{id}/detach` — release a chunk's live route (D-088), through the
 * generated client (bzh:generated-client). 404 for an unknown chunk and 409 for a
 * chunk with no live route both surface as a thrown error — the caller reports it,
 * nothing here swallows it. On success it re-reads the fleet list, the ready queue,
 * and the chunk detail; the endpoint's `chunk_changed`/`queue_changed` SSE frames
 * corroborate for every other open view (no polling, no new hub surface).
 */
export function injectDetachChunkMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: DetachVars): Promise<void> => {
      const { error } = await detachChunkApiChunksChunkIdDetachPost({
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

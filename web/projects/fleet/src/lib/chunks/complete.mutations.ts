import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { completeChunkApiChunksChunkIdCompletePost } from '../api/hub';
import { hubChunkKey, hubChunksKey, hubQueueKey } from '../query-keys';

/** Manually complete a chunk — the board's counterpart of `blizzard hub chunk done`
 * (issue #294). Reachable from any non-`done` status, including `stopped`: unlike Stop,
 * there is no un-complete verb. */
export interface CompleteVars {
  readonly chunkId: string;
}

/**
 * `POST /api/chunks/{id}/complete` — record the chunk's `chunk_completed` fact, through
 * the generated client (bzh:generated-client). Idempotent: completing an already-`done`
 * chunk is a harmless no-op, never a thrown error; a 404 for an unknown chunk still
 * surfaces for the caller to report. On success it re-reads the fleet list, the ready
 * queue, and the chunk detail; the endpoint's `chunk_changed`/`queue_changed` SSE frames
 * corroborate for every other open view (no polling, no new hub surface). `by` is fixed
 * to `operator` here, the same convention `injectChunkPauseMutation` follows — the board
 * has no per-identity `by` field to send.
 */
export function injectCompleteChunkMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: CompleteVars): Promise<void> => {
      const { error } = await completeChunkApiChunksChunkIdCompletePost({
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

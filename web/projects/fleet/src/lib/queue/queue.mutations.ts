import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  type ChunkGroupResponse,
  type QueueReorderResponse,
  groupChunksApiChunksChunkIdGroupPost,
  reorderQueueApiQueueReorderPost,
} from '../api/hub';
import { hubChunksKey, hubQueueKey } from '../query-keys';

/** Move a ready chunk to a queue position (0 = top) — the board's Prioritize control (D-048). */
export interface ReorderVars {
  readonly chunkId: string;
  readonly position: number;
}

/**
 * `POST /api/queue/reorder` — reorder the ready queue through the generated client
 * (bzh:generated-client). On success it re-peeks the queue and re-reads the fleet
 * list; the live stream will also fire `queue-changed`, so this is belt-and-braces.
 */
export function injectReorderQueueMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: ReorderVars): Promise<QueueReorderResponse> => {
      const { data, error } = await reorderQueueApiQueueReorderPost({
        body: { chunk_id: vars.chunkId, position: vars.position },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: hubQueueKey });
      void queryClient.invalidateQueries({ queryKey: hubChunksKey });
    },
  }));
}

/** Group unacquired chunks into a survivor — the board's Group control (D-047/D-048). */
export interface GroupVars {
  readonly survivorId: string;
  readonly mergeChunkIds: readonly string[];
}

/**
 * `POST /api/chunks/{chunk_id}/group` — merge the named unacquired chunks into the
 * survivor (path param), whose PM pointers become the union; the merged-away chunks
 * are discarded (D-047). Re-peeks the queue and re-reads the list on success.
 */
export function injectGroupChunksMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: GroupVars): Promise<ChunkGroupResponse> => {
      const { data, error } = await groupChunksApiChunksChunkIdGroupPost({
        path: { chunk_id: vars.survivorId },
        body: { merge_chunk_ids: [...vars.mergeChunkIds] },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: hubQueueKey });
      void queryClient.invalidateQueries({ queryKey: hubChunksKey });
    },
  }));
}

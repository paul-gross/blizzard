import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  type ChunkGroupResponse,
  type QueuePeekResponse,
  groupChunksApiChunksChunkIdGroupPost,
  repositionQueueApiQueuePositionPost,
} from '../api/hub';
import { hubChunksKey, hubQueueKey } from '../query-keys';

/** Move a ready chunk to sit immediately after `afterChunkId` — `null` is the very
 * top of the queue. The board's READY-lane drag-and-drop and its Top button. */
export interface RepositionVars {
  readonly chunkId: string;
  readonly afterChunkId: string | null;
}

/**
 * `POST /api/queue/position` — a single-chunk reposition against an **anchor**
 * (issue #137), through the generated client (bzh:generated-client).
 *
 * The board expresses one move at a time, so this sends exactly that and lets the
 * hub place it: no whole-order array composed client-side off a possibly-stale
 * cached queue, where a chunk enqueued between the read and the write would be
 * silently dropped out of the order. Move-to-top is the same call with a `null`
 * anchor. On success it invalidates the queue and the fleet list; the live stream
 * will also fire `queue-changed`, so this is belt-and-braces.
 */
export function injectRepositionQueueMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: RepositionVars): Promise<QueuePeekResponse> => {
      const { data, error } = await repositionQueueApiQueuePositionPost({
        body: { chunk_id: vars.chunkId, after_chunk_id: vars.afterChunkId },
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

/** Group unacquired chunks into a survivor — the board's Group control. */
export interface GroupVars {
  readonly survivorId: string;
  readonly mergeChunkIds: readonly string[];
}

/**
 * `POST /api/chunks/{chunk_id}/group` — merge the named unacquired chunks into the
 * survivor (path param), whose work refs become the union; the merged-away chunks
 * are discarded. Re-peeks the queue and re-reads the list on success.
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

import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  type ChunkGroupResponse,
  type QueuePeekEntry,
  type QueuePeekResponse,
  groupChunksApiChunksChunkIdGroupPost,
  replaceQueueApiQueuePut,
} from '../api/hub';
import { hubChunksKey, hubQueueKey } from '../query-keys';

/** Move a ready chunk to a queue position (0 = top) — the board's Prioritize control. */
export interface ReorderVars {
  readonly chunkId: string;
  readonly position: number;
}

/**
 * `PUT /api/queue` — a whole-order replace (issue #104's R1), through the
 * generated client (bzh:generated-client); the single-move `POST /api/queue/reorder`
 * was removed in issue #105, so whole-order replace is the only queue-shaping write.
 * The board's move-to-position control only expresses a single move, so this composes
 * the full order client-side from the currently-cached queue: the named chunk is spliced out
 * and reinserted at the clamped target index, every other ready chunk keeping
 * its current relative order. On success it invalidates the queue and the
 * fleet list; the live stream will also fire `queue-changed`, so this is
 * belt-and-braces.
 */
export function injectReorderQueueMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: ReorderVars): Promise<QueuePeekResponse> => {
      const current = queryClient.getQueryData<QueuePeekEntry[]>(hubQueueKey) ?? [];
      const rest = current.filter((entry) => entry.chunk_id !== vars.chunkId).map((entry) => entry.chunk_id);
      const index = Math.min(Math.max(vars.position, 0), rest.length);
      const chunkIds = [...rest.slice(0, index), vars.chunkId, ...rest.slice(index)];
      const { data, error } = await replaceQueueApiQueuePut({
        body: { chunk_ids: chunkIds },
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
 * survivor (path param), whose PM pointers become the union; the merged-away chunks
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

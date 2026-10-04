import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { type BacklogPeekResponse, type QueuePeekResponse, repositionBacklogApiBacklogPositionPost, repositionQueueApiQueuePositionPost, hubBacklogKey, hubChunksKey, hubQueueKey } from 'fleet';
import { repositionBacklogMutationKey, repositionQueueMutationKey } from '../../core/mutation-keys';

/** Move a chunk to sit immediately after `afterChunkId` — `null` is the very top
 * of the list. Shared by both {@link injectRepositionQueueMutation} (the ready
 * queue) and {@link injectRepositionBacklogMutation} (the backlog): the two
 * routes take the same shape (`bzh:ranking-is-per-list`), so one interface
 * serves the READY and BACKLOG lanes' drag-and-drop. */
export interface RepositionVars {
  readonly chunkId: string;
  readonly afterChunkId: string | null;
}

/**
 * `POST /api/queue/position` — a single-chunk reposition against an **anchor**,
 * through the generated client (bzh:generated-client).
 *
 * Sends exactly the one anchor move and lets the hub place it — pinned by
 * `queue.mutations.spec.ts`'s "posts only the single anchor move, never an order composed
 * from the cached queue". On settle it invalidates the queue and the fleet list.
 */
export function injectRepositionQueueMutation(onError?: (error: Error) => void) {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: repositionQueueMutationKey,
    // QueryClient-wide, so rapid drags remain ordered across component remounts.
    // A queued mutation is still pending, so its requested position renders now.
    scope: { id: 'hub-reposition-queue' },
    mutationFn: async (vars: RepositionVars): Promise<QueuePeekResponse> => {
      const { data, error } = await repositionQueueApiQueuePositionPost({
        body: { chunk_id: vars.chunkId, after_chunk_id: vars.afterChunkId },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubQueueKey }),
        queryClient.invalidateQueries({ queryKey: hubChunksKey }),
      ]),
    ...(onError === undefined ? {} : { onError }),
  }));
}

/**
 * `POST /api/backlog/position` — the backlog's own single-chunk reposition
 * against an anchor, the `not_ready`-list counterpart of
 * {@link injectRepositionQueueMutation} (`bzh:ranking-is-per-list`). Reuses
 * {@link RepositionVars} rather than a duplicate interface: both routes take the
 * same `{chunkId, afterChunkId}` shape. Serves the BACKLOG lane's drag-and-drop
 * itself. On success invalidates the backlog read and the fleet list;
 * the live stream also fires `queue-changed`, so this is belt-and-braces.
 */
export function injectRepositionBacklogMutation(onError?: (error: Error) => void) {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: repositionBacklogMutationKey,
    scope: { id: 'hub-reposition-backlog' },
    mutationFn: async (vars: RepositionVars): Promise<BacklogPeekResponse> => {
      const { data, error } = await repositionBacklogApiBacklogPositionPost({
        body: { chunk_id: vars.chunkId, after_chunk_id: vars.afterChunkId },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubBacklogKey }),
        queryClient.invalidateQueries({ queryKey: hubChunksKey }),
      ]),
    ...(onError === undefined ? {} : { onError }),
  }));
}

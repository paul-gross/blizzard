import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { deleteChunkApiChunksChunkIdDelete } from 'fleet';
import { chunkDeleteMutationKey } from '../../core/mutation-keys';
import { hubBacklogKey, hubChunkKey, hubChunksKey, hubQueueKey } from '../../core/query-keys';

/** Delete an unacquired chunk — the board's counterpart of
 * `blizzard hub chunk delete`. Withdraws the chunk's hub item(s); there is no undo.
 * Reachable only from `not_ready`/`ready`, mirroring Detach's own live-route
 * guard: an unacquired chunk has no route to release, so it has no runner to
 * protect from an in-flight delete either. */
export interface DeleteVars {
  readonly chunkId: string;
}

/**
 * `DELETE /api/chunks/{id}` — withdraw an unacquired chunk's hub item(s), through the
 * generated client (bzh:generated-client). The body names no actor — the hub records
 * the signed-in caller, as it does for {@link injectPromoteChunkMutation}/{@link injectDetachChunkMutation}
 * and every other board write. 404 for an unknown chunk and 409 for an already-acquired
 * one both surface as a thrown error — the caller reports it, nothing here swallows it.
 * On success it re-reads the fleet list, the ready queue, and the backlog — the deleted
 * chunk leaves whichever of the two lists it sat in, `not_ready` or `ready`, unlike
 * Detach/Promote which each touch only the one list their own write can affect — plus
 * the chunk detail; after success its queries are marked stale without refetching the
 * deleted chunk while the dock closes. The endpoint's `chunk-changed` SSE frame corroborates for every
 * other open view (no polling, no new hub surface).
 */
export function injectDeleteChunkMutation(onDeleted?: (chunkId: string) => void) {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: chunkDeleteMutationKey,
    mutationFn: async (vars: DeleteVars): Promise<void> => {
      const { error } = await deleteChunkApiChunksChunkIdDelete({
        path: { chunk_id: vars.chunkId },
        body: {},
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSuccess: (_data, vars) => onDeleted?.(vars.chunkId),
    onSettled: (_data, error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubChunksKey }),
        queryClient.invalidateQueries({ queryKey: hubQueueKey }),
        queryClient.invalidateQueries({ queryKey: hubBacklogKey }),
        queryClient.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId), refetchType: error ? 'active' : 'none' }),
      ]),
  }));
}

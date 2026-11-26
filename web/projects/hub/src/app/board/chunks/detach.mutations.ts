import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { detachChunkApiChunksChunkIdDetachPost } from 'fleet/hub-api';
import { chunkDetachMutationKey } from '../../core/mutation-keys';
import { hubChunkKey, hubChunksKey, hubQueueKey } from '../../core/query-keys';

/** The chunk to release from its live route. */
export interface DetachVars {
  readonly chunkId: string;
}

/**
 * `POST /api/chunks/{id}/detach`, through the generated client (bzh:generated-client).
 * A refusal throws; it settles by re-reading the fleet list, the ready queue, and the
 * chunk detail.
 */
export function injectDetachChunkMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: chunkDetachMutationKey,
    mutationFn: async (vars: DetachVars): Promise<void> => {
      const { error } = await detachChunkApiChunksChunkIdDetachPost({
        path: { chunk_id: vars.chunkId },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubChunksKey }),
        queryClient.invalidateQueries({ queryKey: hubQueueKey }),
        queryClient.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId) }),
      ]),
  }));
}

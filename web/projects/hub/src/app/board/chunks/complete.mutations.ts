import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { completeChunkApiChunksChunkIdCompletePost, hubChunkKey, hubChunksKey, hubQueueKey } from 'fleet';
import { chunkCompleteMutationKey } from '../../core/mutation-keys';

/** The chunk to complete by hand. */
export interface CompleteVars {
  readonly chunkId: string;
}

/**
 * `POST /api/chunks/{id}/complete` as `operator`, through the generated client
 * (bzh:generated-client). A refusal throws; it settles by re-reading the fleet list,
 * the ready queue, and the chunk detail.
 */
export function injectCompleteChunkMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: chunkCompleteMutationKey,
    mutationFn: async (vars: CompleteVars): Promise<void> => {
      const { error } = await completeChunkApiChunksChunkIdCompletePost({
        path: { chunk_id: vars.chunkId },
        body: { by: 'operator' },
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

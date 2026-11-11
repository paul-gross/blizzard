import { inject } from '@angular/core';
import { injectMutation, QueryClient } from '@tanstack/angular-query-experimental';
import { hubBacklogKey, hubChunkKey, hubChunksKey, hubQueueKey, patchChunkApiChunksChunkIdPatch } from 'fleet';
import { chunkUrgencyMutationKey } from '../../core/mutation-keys';

export interface ChunkUrgencyVars { readonly chunkId: string; readonly urgencyId: string }

export function injectChunkUrgencyMutation() {
  const client = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: chunkUrgencyMutationKey,
    mutationFn: async ({ chunkId, urgencyId }: ChunkUrgencyVars): Promise<void> => {
      const { error } = await patchChunkApiChunksChunkIdPatch({ path: { chunk_id: chunkId }, body: { urgency: urgencyId }, throwOnError: false });
      if (error) throw error;
    },
    onSettled: (_data, _error, vars) => Promise.all([
      client.invalidateQueries({ queryKey: hubChunksKey }),
      client.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId) }),
      client.invalidateQueries({ queryKey: hubQueueKey }),
      client.invalidateQueries({ queryKey: hubBacklogKey }),
    ]),
  }));
}

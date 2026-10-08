import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { patchChunkApiChunksChunkIdPatch, hubChunkKey, hubChunksKey } from 'fleet';
import { chunkSetGraphMutationKey } from '../../core/mutation-keys';

/** Repin a not-ready chunk's workflow graph — the target graph's id. */
export interface ChunkGraphEditVars {
  readonly chunkId: string;
  readonly graphId: string;
}

/**
 * `PATCH /api/chunks/{id}` with `{ graph_id }`, through the generated client
 * (bzh:generated-client). A refusal throws; it settles by re-reading the fleet list and
 * the chunk detail.
 */
export function injectSetChunkGraphMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: chunkSetGraphMutationKey,
    mutationFn: async (vars: ChunkGraphEditVars): Promise<void> => {
      const { error } = await patchChunkApiChunksChunkIdPatch({
        path: { chunk_id: vars.chunkId },
        body: { graph_id: vars.graphId },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubChunksKey }),
        queryClient.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId) }),
      ]),
  }));
}

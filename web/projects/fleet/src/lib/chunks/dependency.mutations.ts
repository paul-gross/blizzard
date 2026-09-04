import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { declareDependencyApiChunksChunkIdDependenciesPost, releaseDependencyApiChunksChunkIdDependenciesReleasePost } from '../api/hub';
import { hubBacklogKey, hubChunkKey, hubChunksKey, hubQueueKey } from '../query-keys';

export interface DependencyVars {
  readonly chunkId: string;
  readonly prerequisiteChunkId: string;
}

/** Both the declared edge and its release change what a peek/board read derives for
 * every chunk that names or is named by it, so both mutations invalidate the same set
 * `promote.mutations.ts` does: both ranked lists and the chunk itself. Neither emits an
 * SSE frame of its own (`chunk_dependencies.py`), so this invalidation is the acting
 * client's only path to freshness. */
function invalidateDependencyQueries(queryClient: QueryClient, vars: DependencyVars): void {
  void queryClient.invalidateQueries({ queryKey: hubChunksKey });
  void queryClient.invalidateQueries({ queryKey: hubQueueKey });
  void queryClient.invalidateQueries({ queryKey: hubBacklogKey });
  void queryClient.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId) });
}

/** Declare that `chunkId` depends on `prerequisiteChunkId` (issue #461). The hub answers
 * 409 for a dependent past its pre-claim window, a cycle the edge would close, or an
 * ephemeral prerequisite, and 404 for either unknown chunk — every one of those reads
 * off `error.detail` through `errorMessage`, the same as any other dock action. */
export function injectDeclareDependencyMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: DependencyVars): Promise<void> => {
      const { error } = await declareDependencyApiChunksChunkIdDependenciesPost({
        path: { chunk_id: vars.chunkId },
        body: { prerequisite_chunk_id: vars.prerequisiteChunkId, by: 'operator' },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSuccess: (_data, vars) => invalidateDependencyQueries(queryClient, vars),
  }));
}

/** Release `chunkId`'s standing dependency on `prerequisiteChunkId` (issue #461). The hub
 * answers 409 when no such edge stands and 404 for an unknown dependent. */
export function injectReleaseDependencyMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: DependencyVars): Promise<void> => {
      const { error } = await releaseDependencyApiChunksChunkIdDependenciesReleasePost({
        path: { chunk_id: vars.chunkId },
        body: { prerequisite_chunk_id: vars.prerequisiteChunkId, by: 'operator' },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSuccess: (_data, vars) => invalidateDependencyQueries(queryClient, vars),
  }));
}

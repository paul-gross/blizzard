import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { chunkRequeueMutationKey } from '../../core/mutation-keys';
import { runnerChunkDetailKey, runnerDashboardKey, runnerLeasesKey } from '../../core/query-keys';

/** Requeue `chunkId` on `runnerId`, the runner that owns the chunk's escalation. */
export interface ChunkRequeueVars {
  readonly runnerId: string;
  readonly chunkId: string;
}

/**
 * `POST /runners/{runner_id}/api/chunks/{chunk_id}/requeues` — the route `blizzard runner requeue`
 * uses, called through the generated `requeueChunkRunnersRunnerIdApiChunksChunkIdRequeuesPost`
 * (`bzh:generated-client`). Whether the requeue is legal — no open takeover, no held environment —
 * is the runner's judgement, answered with a 409. On settle it re-reads that runner's dashboard
 * (its escalations), its leases and the chunk's detail. Keyed by `runnerId`.
 */
export function injectChunkRequeueMutation(runnerId: () => string | null) {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: chunkRequeueMutationKey(runnerId()),
    mutationFn: async (vars: ChunkRequeueVars): Promise<runnerApi.RequeueResponse> => {
      const { data, error } = await runnerApi.requeueChunkRunnersRunnerIdApiChunksChunkIdRequeuesPost({
        path: { runner_id: vars.runnerId, chunk_id: vars.chunkId },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: runnerDashboardKey(vars.runnerId) }),
        queryClient.invalidateQueries({ queryKey: runnerLeasesKey(vars.runnerId) }),
        queryClient.invalidateQueries({ queryKey: runnerChunkDetailKey(vars.runnerId, vars.chunkId) }),
      ]),
  }));
}

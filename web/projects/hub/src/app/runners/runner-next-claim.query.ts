import { injectQuery } from '@tanstack/angular-query-experimental';
import { injectQueries } from '@tanstack/angular-query-experimental/inject-queries-experimental';
import { prerequisiteStatuses } from './runner-next-claim.model';
import type { QueryObserverResult } from '@tanstack/angular-query-experimental';
import { hubApi, hubChunkKey, hubRunnerNextClaimKey, LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet';

/** The hub's projection is authoritative; the client does not repeat the claim walk. */
export function injectRunnerNextClaimQuery(runnerId: () => string) {
  return injectQuery(() => {
    const id = runnerId();
    return {
      queryKey: [...hubRunnerNextClaimKey, id],
      queryFn: async (): Promise<hubApi.NextClaimResponse> => {
        const { data, error } = await hubApi.getRunnerNextClaimApiRunnersRunnerIdNextClaimGet({
          path: { runner_id: id },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}

/** Read prerequisites individually from the tenant chunk endpoint, not the board window. */
export function injectPrerequisiteStatuses(ids: () => readonly string[]) {
  return injectQueries(() => ({
    queries: ids().map((id) => ({
      queryKey: hubChunkKey(id),
      queryFn: async () => {
        const { data, error } = await hubApi.getChunkApiChunksChunkIdGet({ path: { chunk_id: id }, throwOnError: false });
        if (error) throw error;
        return data!;
      },
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    })),
    combine: (results) => prerequisiteStatuses(results as unknown as readonly QueryObserverResult<hubApi.ChunkDetail>[]),
  }));
}

import { injectQuery } from '@tanstack/angular-query-experimental';
import { injectQueries } from '@tanstack/angular-query-experimental/inject-queries-experimental';
import { prerequisiteStatuses } from './runner-next-claim.model';
import type { QueryObserverResult } from '@tanstack/angular-query-experimental';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet/core';
import { hubApi } from 'fleet/hub-api';
import { hubChunkKey, hubRunnerNextClaimKey } from '../core/query-keys';
import type { PrerequisiteStatus } from './runner-next-claim.domain';
import { toNextClaim, toPrerequisiteStatus } from './runner-next-claim.translate';

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
      select: toNextClaim,
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
      select: toPrerequisiteStatus,
    })),
    combine: (results) => prerequisiteStatuses(results as unknown as readonly QueryObserverResult<PrerequisiteStatus>[]),
  }));
}

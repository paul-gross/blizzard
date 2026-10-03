import { injectQuery } from '@tanstack/angular-query-experimental';

import { chunkCountsApiChunkCountsGet, type ChunkCountsView, LIVE_COVERED_POLL_BACKSTOP_MS, hubChunkCountsKey } from 'fleet/shell';

/**
 * Hub `GET /api/chunk-counts` read — the all-time fleet count per chunk status, through
 * TanStack Query and the generated hub client. The board list is windowed, so every
 * count the UI shows comes from here, never from the list's length.
 */
export function injectHubChunkCountsQuery() {
  return injectQuery(() => ({
    queryKey: hubChunkCountsKey,
    queryFn: async (): Promise<ChunkCountsView> => {
      const { data } = await chunkCountsApiChunkCountsGet({ throwOnError: true });
      return data;
    },
    // Same backstop as the chunk list it sits beside — the live events refresh it first.
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

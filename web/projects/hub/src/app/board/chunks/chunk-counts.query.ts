import { injectQuery } from '@tanstack/angular-query-experimental';

import { chunkCountsApiChunkCountsGet, type ChunkCountsView, LIVE_COVERED_POLL_BACKSTOP_MS, hubChunkCountsKey } from 'fleet/shell';

/**
 * Hub `GET /api/chunk-counts` read — the all-time fleet count per chunk status, through
 * TanStack Query and the generated hub client. The board list is windowed, so every
 * count the UI shows comes from here, never from the list's length. `enabled` holds the read back while it is false —
 * the app root reads it before the tenant it is scoped to may be settled.
 */
export function injectHubChunkCountsQuery(enabled: () => boolean = () => true) {
  return injectQuery(() => ({
    queryKey: hubChunkCountsKey,
    enabled: enabled(),
    queryFn: async (): Promise<ChunkCountsView> => {
      const { data } = await chunkCountsApiChunkCountsGet({ throwOnError: true });
      return data;
    },
    // Same backstop as the chunk list it sits beside — the live events refresh it first.
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

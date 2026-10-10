import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  chunkCountsApiChunkCountsGet,
  type ChunkCountsView,
  LIVE_COVERED_POLL_BACKSTOP_MS,
  hubChunkCountsKey,
  injectProjectFilter,
  lensedKey,
} from 'fleet/shell';

/**
 * Hub `GET /api/chunk-counts` read — the all-time count per chunk status, through
 * TanStack Query and the generated hub client. The board list is windowed, so every
 * count the UI shows comes from here, never from the list's length. Under a project lens the read sends the lens's
 * `?project=` and keys on it, so the header counts the lensed project's chunks only. `enabled` holds the read back while
 * it is false — the app root reads it before the tenant it is scoped to may be settled.
 */
export function injectHubChunkCountsQuery(enabled: () => boolean = () => true) {
  const projectFilter = injectProjectFilter();
  return injectQuery(() => {
    const project = projectFilter();
    return {
      queryKey: lensedKey(hubChunkCountsKey, project),
      enabled: enabled(),
      queryFn: async (): Promise<ChunkCountsView> => {
        const { data } = await chunkCountsApiChunkCountsGet({
          query: { project: project ?? undefined },
          throwOnError: true,
        });
        return data;
      },
      // Same backstop as the chunk list it sits beside — the live events refresh it first.
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}

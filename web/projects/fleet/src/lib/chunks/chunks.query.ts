import { injectQuery } from '@tanstack/angular-query-experimental';

import { listChunksApiChunksGet, type ChunkSummary } from '../api/hub';
import { hubChunksKey } from '../query-keys';

/**
 * Hub `GET /api/chunks` read — the fleet chunk list (derived status + current
 * node, D-004), through TanStack Query and the generated hub client
 * (D-097/D-100). Like the health read this is real plumbing: the request is the
 * openapi-ts SDK call (never hand-written fetch, bzh:generated-client) and it hits
 * the daemon the app is served from. Returns the typed `ChunkSummary[]`; an empty
 * fleet is an empty array, not an error.
 */
export function injectHubChunksQuery() {
  return injectQuery(() => ({
    queryKey: hubChunksKey,
    queryFn: async (): Promise<ChunkSummary[]> => {
      const { data, error } = await listChunksApiChunksGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
    refetchInterval: 3000,
  }));
}

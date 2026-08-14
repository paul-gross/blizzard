import { injectQuery } from '@tanstack/angular-query-experimental';

import { listChunksApiChunksGet, type ChunkSummary } from '../api/hub';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from '../polling';
import { hubChunksKey } from '../query-keys';

/**
 * Hub `GET /api/chunks` read — the fleet chunk list (derived status + current
 * node), through TanStack Query and the generated hub client.
 * Like the health read this is real plumbing: the request is the
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
    // Covered by chunk-changed, question-asked/-answered, and decision-opened/-resolved
    // (EVENT_INVALIDATION_REGISTRY, sse/fleet-live.ts) — this is the backstop, not the
    // primary freshness path. See LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

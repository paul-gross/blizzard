import { injectQuery } from '@tanstack/angular-query-experimental';

import { listChunksApiChunksGet, type ChunkSummary } from '../api/hub';
import { DRAIN_LIMIT, drainPages } from '../paginated-read';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from '../polling';
import { hubChunksKey } from '../query-keys';

/**
 * Hub `GET /api/chunks` read — the fleet chunk list (derived status + current
 * node), through TanStack Query and the generated hub client.
 * Like the health read this is real plumbing: the request is the
 * openapi-ts SDK call (never hand-written fetch, bzh:generated-client) and it hits
 * the daemon the app is served from. The read is keyset-paginated on the hub
 * (blizzard#526); {@link drainPages} follows `next_cursor` to exhaustion so this
 * query still resolves the typed `ChunkSummary[]` whole. An empty fleet is an empty
 * array, not an error.
 */
export function injectHubChunksQuery() {
  return injectQuery(() => ({
    queryKey: hubChunksKey,
    queryFn: (): Promise<ChunkSummary[]> =>
      drainPages(
        (cursor) => listChunksApiChunksGet({ query: { cursor, limit: DRAIN_LIMIT }, throwOnError: false }),
        (page) => page.chunks,
      ),
    // Covered by chunk-changed, question-asked/-answered, and decision-opened/-resolved
    // (EVENT_INVALIDATION_REGISTRY, sse/fleet-live.ts) — this is the backstop, not the
    // primary freshness path. See LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

import { injectQuery } from '@tanstack/angular-query-experimental';

import { listChunksApiChunksGet, type ChunkSummary, LIVE_COVERED_POLL_BACKSTOP_MS, hubBoardChunksKey } from 'fleet/shell';
import { DRAIN_LIMIT, drainPages } from '../../paginated-read';

/**
 * Hub `GET /api/chunks?board_window=true` read — the board's chunk list (derived
 * status + current node): every chunk but a `done` one finished more than 48 hours ago,
 * through TanStack Query and the generated hub client.
 * Like the health read this is real plumbing: the request is the
 * openapi-ts SDK call (never hand-written fetch, bzh:generated-client) and it hits
 * the daemon the app is served from. The read is keyset-paginated on the hub;
 * {@link drainPages} follows `next_cursor` to exhaustion so this
 * query still resolves the typed `ChunkSummary[]` whole. An empty fleet is an empty
 * array, not an error.
 */
export function injectHubBoardChunksQuery() {
  return injectQuery(() => ({
    queryKey: hubBoardChunksKey,
    queryFn: (): Promise<ChunkSummary[]> =>
      drainPages(
        (cursor) => listChunksApiChunksGet({ query: { cursor, limit: DRAIN_LIMIT, board_window: true }, throwOnError: false }),
        (page) => page.chunks,
      ),
    // Covered by chunk-changed, question-asked/-answered, and decision-opened/-resolved
    // (EVENT_INVALIDATION_REGISTRY, sse/fleet-live.ts) — this is the backstop, not the
    // primary freshness path. See LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

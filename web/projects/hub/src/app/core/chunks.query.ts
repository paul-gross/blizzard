import { type QueryClient, injectQuery } from '@tanstack/angular-query-experimental';

import {
  listChunksApiChunksGet,
  type ChunkSummary,
  LIVE_COVERED_POLL_BACKSTOP_MS,
  hubBoardChunksKey,
  injectProjectFilter,
  lensedKey,
} from 'fleet/shell';
import { DRAIN_LIMIT, drainPages } from './paginated-read';

/**
 * The board's chunk list as one query, for the project `project` names (`null` for the whole tenant): `GET
 * /api/chunks?board_window=true`, with `?project=` under a lens, keyed by the lens so each project caches apart.
 */
export function boardChunksQueryOptions(project: string | null) {
  return {
    queryKey: lensedKey(hubBoardChunksKey, project),
    queryFn: (): Promise<ChunkSummary[]> =>
      drainPages(
        (cursor) =>
          listChunksApiChunksGet({
            query: { cursor, limit: DRAIN_LIMIT, board_window: true, project: project ?? undefined },
            throwOnError: false,
          }),
        (page) => page.chunks,
      ),
  };
}

/**
 * Hub `GET /api/chunks?board_window=true` read — the board's chunk list (derived
 * status + current node): every chunk but a `done` one finished more than 48 hours ago that is not among the 10 newest finished,
 * through TanStack Query and the generated hub client. Under a project lens it reads only the lensed project's chunks,
 * in the same order — the lens filters, it never re-ranks.
 * Like the health read this is real plumbing: the request is the
 * openapi-ts SDK call (never hand-written fetch, bzh:generated-client) and it hits
 * the daemon the app is served from. The read is keyset-paginated on the hub;
 * {@link drainPages} follows `next_cursor` to exhaustion so this
 * query still resolves the typed `ChunkSummary[]` whole. An empty fleet is an empty
 * array, not an error.
 */
export function injectHubBoardChunksQuery() {
  const projectFilter = injectProjectFilter();
  return injectQuery(() => ({
    ...boardChunksQueryOptions(projectFilter()),
    // Covered by chunk-changed, question-asked/-answered, and decision-opened/-resolved
    // (EVENT_INVALIDATION_REGISTRY, sse/fleet-live.ts) — this is the backstop, not the
    // primary freshness path. See LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/**
 * Rows that name a chunk, narrowed to the chunks of the project `project` names — every row on All (`null`). The
 * questions and decisions reads carry no project of their own, so a row belongs to a project through its chunk: it is
 * kept when its chunk is on the project's board list, read through {@link boardChunksQueryOptions} (the board's own
 * cache entry, re-read only when it is missing or a live event has staled it).
 */
export async function inProjectChunks<T extends { readonly chunk_id: string }>(
  queryClient: QueryClient,
  project: string | null,
  rows: readonly T[],
): Promise<T[]> {
  if (project === null) return [...rows];
  const chunks = await queryClient.fetchQuery({ ...boardChunksQueryOptions(project), staleTime: Infinity });
  const ids = new Set(chunks.map((chunk) => chunk.chunk_id));
  return rows.filter((row) => ids.has(row.chunk_id));
}

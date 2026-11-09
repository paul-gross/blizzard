import type { hubApi } from 'fleet';
import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  listChunksApiChunksGet,
  LIVE_COVERED_POLL_BACKSTOP_MS,
  hubBoardChunksKey,
  injectProjectFilter,
  lensedKey,
} from 'fleet/shell';
import { DRAIN_LIMIT, drainPages } from './paginated-read';
import type { BoardChunksRead } from './chunks.model';

/**
 * The board's chunk list as one query, for the project `project` names (`null` for the whole tenant): `GET
 * /api/chunks?board_window=true`, with `?project=` under a lens, keyed by the lens so each project caches apart.
 */
export function boardChunksQueryOptions(project: string | null) {
  return {
    queryKey: lensedKey(hubBoardChunksKey, project),
    queryFn: async (): Promise<BoardChunksRead> => {
      const pages = await drainPages(
        (cursor) =>
          listChunksApiChunksGet({
            query: {
              cursor,
              limit: DRAIN_LIMIT,
              board_window: true,
              project: project ?? undefined,
            },
            throwOnError: false,
          }),
        (page): hubApi.ChunksPageView[] => [page],
      );
      return { chunks: pages.flatMap((page) => page.chunks), pages };
    },
  };
}

/**
 * Hub `GET /api/chunks?board_window=true` read — the board's chunk list (derived
 * status + current node). Under a project lens it reads only the lensed project's chunks,
 * in the same order. {@link drainPages} follows `next_cursor` to exhaustion so this
 * query resolves the chunks and original pages whole.
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

import type { hubApi } from 'fleet';
import { injectQuery } from '@tanstack/angular-query-experimental';

import { listChunksApiChunksGet, LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet/shell';
import { DRAIN_LIMIT, drainPages } from './paginated-read';
import { hubBoardChunksKey, lensedKey } from './query-keys';
import { injectProjectFilter } from './project-filter';
import { fromBoardPage, fromBoardSummary } from './chunks.translate';

export interface BoardChunksRead {
  readonly chunks: hubApi.ChunkSummary[];
  readonly pages: readonly hubApi.ChunksPageView[];
}
/**
 * The board's chunk list as one query, for the project `project` names (`null` for the whole tenant): `GET
 * /api/chunks?board_window=true`, with `?project=` under a lens, keyed by the lens so each project caches apart.
 */
export function boardChunksReadOptions(project: string | null) {
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

/** Domain observer over the shared lensed wire cache. */
export function domainBoardChunksQueryOptions(project: string | null) {
  return {
    ...boardChunksReadOptions(project),
    select: (read: BoardChunksRead) => ({
      chunks: read.chunks.map(fromBoardSummary),
      pages: read.pages.map(fromBoardPage),
    }),
  };
}

export function injectDomainBoardChunksQuery() {
  const projectFilter = injectProjectFilter();
  return injectQuery(() => ({
    ...domainBoardChunksQueryOptions(projectFilter()),
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

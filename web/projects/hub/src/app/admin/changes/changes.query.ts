import { injectInfiniteQuery } from '@tanstack/angular-query-experimental';

import { type ConfigChangesPage, hubConfigChangesKey, listChangesApiConfigChangesGet } from 'fleet';

/** The page size the change log asks for. */
const CHANGES_PAGE = 50;

/**
 * `GET /api/config/changes` — every change to every config record, newest first, a
 * page at a time: each next page asks for the changes `before` the previous page's
 * `next_before`, and a page with none is the last. No SSE event names a config
 * change, so a write made through another door shows on the next refetch.
 */
export function injectConfigChangesQuery() {
  return injectInfiniteQuery(() => ({
    queryKey: hubConfigChangesKey,
    initialPageParam: undefined as number | undefined,
    queryFn: async ({ pageParam }: { pageParam: number | undefined }): Promise<ConfigChangesPage> => {
      const { data, error } = await listChangesApiConfigChangesGet({
        query: { before: pageParam, limit: CHANGES_PAGE },
        throwOnError: false,
      });
      if (error) throw error;
      return data ?? { changes: [] };
    },
    getNextPageParam: (last: ConfigChangesPage) => last.next_before ?? undefined,
  }));
}

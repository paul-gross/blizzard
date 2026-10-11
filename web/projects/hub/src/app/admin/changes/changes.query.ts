import { injectInfiniteQuery } from '@tanstack/angular-query-experimental';

import { type ConfigChangesPage, hubConfigChangesKey, lensedKey, listChangesApiConfigChangesGet } from 'fleet';
import { injectProjectFilter } from 'fleet/shell';

/** The page size the change log asks for. */
const CHANGES_PAGE = 50;

/**
 * `GET /api/config/changes` — every change to every config record, newest first, a
 * page at a time: each next page asks for the changes `before` the previous page's
 * `next_before`, and a page with none is the last. Under a project lens it sends
 * `?project=`, which keeps that project's changes and the tenant-wide ones beside them
 * (a source, a repository, a secret). The list and the selected entry read the one cache
 * entry the lens keys. Freshness: `hubConfigKey` (`web/projects/fleet/src/lib/core/query-keys.ts`).
 */
export function injectConfigChangesQuery() {
  const filter = injectProjectFilter();
  return injectInfiniteQuery(() => {
    const project = filter();
    return {
      queryKey: lensedKey(hubConfigChangesKey, project),
      initialPageParam: undefined as number | undefined,
      queryFn: async ({ pageParam }: { pageParam: number | undefined }): Promise<ConfigChangesPage> => {
        const { data, error } = await listChangesApiConfigChangesGet({
          query: { before: pageParam, limit: CHANGES_PAGE, ...(project === null ? {} : { project }) },
          throwOnError: false,
        });
        if (error) throw error;
        return data ?? { changes: [] };
      },
      getNextPageParam: (last: ConfigChangesPage) => last.next_before ?? undefined,
    };
  });
}

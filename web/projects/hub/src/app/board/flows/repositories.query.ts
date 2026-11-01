import { injectQuery } from '@tanstack/angular-query-experimental';

import { hubConfigListKey, listRepositoriesApiRepositoriesGet, type RepositorySummary } from 'fleet';

/** `GET /api/repositories` — the tenant's live repositories, which a lane joins its flow onto to name the forge-side
 * repository a parked chunk's change is on. Shares the admin list's key, so one read serves both. Freshness:
 * `hubConfigKey` (`web/projects/fleet/src/lib/core/query-keys.ts`). */
export function injectLaneRepositoriesQuery() {
  return injectQuery(() => ({
    queryKey: hubConfigListKey('repositories', false),
    queryFn: async (): Promise<RepositorySummary[]> => {
      const { data, error } = await listRepositoriesApiRepositoriesGet({ query: { include_retired: false }, throwOnError: false });
      if (error) throw error;
      return data?.repositories ?? [];
    },
  }));
}

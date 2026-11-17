import { injectQuery } from '@tanstack/angular-query-experimental';

import { listRepositoriesApiRepositoriesGet, type RepositorySummary } from 'fleet';
import { hubConfigListKey } from './query-keys';

/** `GET /api/repositories` — the tenant's live repositories, which the Delivery page joins a flow onto to name the
 * forge-side repository a chunk's change is on. Shares the admin list's key, so one read serves both. Freshness:
 * `hubConfigKey` (`web/projects/hub/src/app/core/query-keys.ts`). */
export function injectLaneRepositoriesQuery() {
  return injectQuery(() => ({
    queryKey: hubConfigListKey('repositories', false),
    queryFn: async (): Promise<RepositorySummary[]> => {
      const { data, error } = await listRepositoriesApiRepositoriesGet({
        query: { include_retired: false },
        throwOnError: false,
      });
      if (error) throw error;
      return data?.repositories ?? [];
    },
  }));
}

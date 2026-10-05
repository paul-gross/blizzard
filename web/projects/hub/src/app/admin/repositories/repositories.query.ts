import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  getRepositoryApiRepositoriesNameGet,
  hubConfigListKey,
  hubConfigRecordKey,
  listRepositoriesApiRepositoriesGet,
  type RepositorySummary,
} from 'fleet';

const NOUN = 'repositories';

/** `GET /api/repositories` — every repository, retired ones too while
 * `includeRetired()` holds. No SSE event names a config change, so a write made
 * through another door shows on the next refetch. */
export function injectRepositoriesQuery(includeRetired: () => boolean) {
  return injectQuery(() => {
    const retired = includeRetired();
    return {
      queryKey: hubConfigListKey(NOUN, retired),
      queryFn: async (): Promise<RepositorySummary[]> => {
        const { data, error } = await listRepositoriesApiRepositoriesGet({
          query: { include_retired: retired },
          throwOnError: false,
        });
        if (error) throw error;
        return data?.repositories ?? [];
      },
    };
  });
}

/** `GET /api/repositories/{name}` — one repository, at rest while `name()` is `null`. */
export function injectRepositoryQuery(name: () => string | null) {
  return injectQuery(() => {
    const recordName = name();
    return {
      queryKey: hubConfigRecordKey(NOUN, recordName),
      enabled: recordName !== null,
      queryFn: async (): Promise<RepositorySummary> => {
        const { data, error } = await getRepositoryApiRepositoriesNameGet({
          path: { name: recordName! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
    };
  });
}

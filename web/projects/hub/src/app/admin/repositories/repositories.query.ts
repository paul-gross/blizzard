import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  getRepositoryApiRepositoriesRepositoryGet,
  hubApi,
  hubConfigKey,
  hubConfigListKey,
  hubConfigRecordKey,
  listRepositoriesApiRepositoriesGet,
  type RepositorySummary,
} from 'fleet';

type LandingPolicyNeedsView = hubApi.LandingPolicyNeedsView;

const NOUN = 'repositories';

/** `GET /api/repositories` — every repository, retired ones too while
 * `includeRetired()` holds. Freshness: `hubConfigKey` (`web/projects/fleet/src/lib/core/query-keys.ts`). */
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

/** `GET /api/repositories/{repository}` — one repository by its id or slug, at rest while `key()` is `null`. */
export function injectRepositoryQuery(key: () => string | null) {
  return injectQuery(() => {
    const recordName = key();
    return {
      queryKey: hubConfigRecordKey(NOUN, recordName),
      enabled: recordName !== null,
      queryFn: async (): Promise<RepositorySummary> => {
        const { data, error } = await getRepositoryApiRepositoriesRepositoryGet({
          path: { repository: recordName! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
    };
  });
}

/** `GET /api/config/landing-policies` — every landing policy with the forge capabilities it requires and
 * uses, in the hub's order. Freshness: `hubConfigKey` (`web/projects/fleet/src/lib/core/query-keys.ts`). */
export function injectLandingPoliciesQuery() {
  return injectQuery(() => ({
    queryKey: [...hubConfigKey, 'landing-policies'],
    queryFn: async (): Promise<LandingPolicyNeedsView[]> => {
      const { data, error } = await hubApi.listLandingPoliciesApiConfigLandingPoliciesGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
  }));
}

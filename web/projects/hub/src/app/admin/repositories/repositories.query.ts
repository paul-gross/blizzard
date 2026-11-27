import { toProjectRepository } from './repositories.translate';
import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  getRepositoryApiRepositoriesRepositoryGet,
  hubApi,
  listRepositoriesApiRepositoriesGet,
  type RepositorySummary,
} from 'fleet/hub-api';
import { hubConfigKey, hubConfigListKey, hubConfigRecordKey } from '../../core/query-keys';
import type { LandingPolicy, Repository } from './repository.domain';
import { fromLandingPolicyNeeds, fromRepositorySummary } from './repository.translate';

type LandingPolicyNeedsView = hubApi.LandingPolicyNeedsView;

const NOUN = 'repositories';

function repositoriesOptions(retired: boolean) {
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
}

/** `GET /api/repositories` — every repository, retired ones too while `includeRetired()` holds, as the wire
 * reads it, for the callers still outside the domain boundary. Freshness: `hubConfigKey`
 * (`web/projects/hub/src/app/core/query-keys.ts`). */
export function injectRepositoriesQuery(includeRetired: () => boolean) {
  return injectQuery(() => repositoriesOptions(includeRetired()));
}

/** {@link injectRepositoriesQuery}'s read, on the same cache key, as domain {@link Repository} records. */
export function injectRepositoryListQuery(includeRetired: () => boolean) {
  return injectQuery(() => ({
    ...repositoriesOptions(includeRetired()),
    select: (repositories: RepositorySummary[]): readonly Repository[] => repositories.map(fromRepositorySummary),
  }));
}

/** `GET /api/repositories/{repository}` — one repository by its id or slug, as a domain {@link Repository},
 * at rest while `key()` is `null`. */
export function injectRepositoryRecordQuery(key: () => string | null) {
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
      select: fromRepositorySummary,
    };
  });
}

/** `GET /api/config/landing-policies` — every landing policy, in the hub's order, as the policies a form may
 * offer. Freshness: `hubConfigKey` (`web/projects/hub/src/app/core/query-keys.ts`). */
export function injectLandingPolicyChoicesQuery() {
  return injectQuery(() => ({
    queryKey: [...hubConfigKey, 'landing-policies'],
    queryFn: async (): Promise<LandingPolicyNeedsView[]> => {
      const { data, error } = await hubApi.listLandingPoliciesApiConfigLandingPoliciesGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
    select: (needs: LandingPolicyNeedsView[]): readonly LandingPolicy[] => fromLandingPolicyNeeds(needs),
  }));
}

/** Project projection over the shared repository cache. */
export function injectProjectRepositoriesQuery(includeRetired: () => boolean) {
  return injectQuery(() => ({
    ...repositoriesOptions(includeRetired()),
    select: (rows: RepositorySummary[]) => rows.map(toProjectRepository),
  }));
}

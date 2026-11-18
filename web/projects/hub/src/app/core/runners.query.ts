import { toProjectRunner } from './runners.translate';
import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  type RunnerRegistryView,
  listRunnersApiRunnersGet,
  LIVE_COVERED_POLL_BACKSTOP_MS,
} from 'fleet/shell';
import { hubRunnersKey } from './query-keys';

/**
 * Hub `GET /api/runners` read — the fleet registry with each runner's derived
 * liveness and `paused` state.
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/hub/src/app/core/live/hub-live-updates.ts`).
 *
 * Retired runners are excluded unless `includeRetired()` is true. The include-retired read keys
 * under {@link hubRunnersKey}, so an invalidation of that key re-reads both.
 */
function HubRunnersQueryOptions(includeRetired: () => boolean = () => false) {
  return {
    queryKey: includeRetired()
      ? [...hubRunnersKey, 'include-retired']
      : hubRunnersKey,
    queryFn: async (): Promise<RunnerRegistryView[]> => {
      const { data, error } = await listRunnersApiRunnersGet({
        query: includeRetired() ? { include_retired: true } : undefined,
        throwOnError: false,
      });
      if (error) throw error;
      return data?.runners ?? [];
    },
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  };
}

export function injectHubRunnersQuery(
  includeRetired: () => boolean = () => false,
) {
  return injectQuery(() => HubRunnersQueryOptions(includeRetired));
}

/** Domain projection over the same cached wire read. */
export function injectProjectRunnersQuery(
  includeRetired: () => boolean = () => false,
) {
  return injectQuery(() => ({
    ...HubRunnersQueryOptions(includeRetired),
    select: (rows: RunnerRegistryView[]) => rows.map(toProjectRunner),
  }));
}

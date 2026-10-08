import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  getWorkSourceApiWorkSourcesSourceGet,
  hubConfigListKey,
  hubConfigRecordKey,
  listWorkSourcesApiWorkSourcesGet,
  type WorkSourceSummary,
} from 'fleet';

const NOUN = 'work-sources';

/** `GET /api/work-sources` — every work source, retired ones too while
 * `includeRetired()` holds. Freshness: `hubConfigKey` (`web/projects/fleet/src/lib/core/query-keys.ts`). */
export function injectWorkSourcesQuery(includeRetired: () => boolean) {
  return injectQuery(() => {
    const retired = includeRetired();
    return {
      queryKey: hubConfigListKey(NOUN, retired),
      queryFn: async (): Promise<WorkSourceSummary[]> => {
        const { data, error } = await listWorkSourcesApiWorkSourcesGet({
          query: { include_retired: retired },
          throwOnError: false,
        });
        if (error) throw error;
        return data?.sources ?? [];
      },
    };
  });
}

/** `GET /api/work-sources/{source}` — one work source, at rest while `name()` is `null`. */
export function injectWorkSourceQuery(name: () => string | null) {
  return injectQuery(() => {
    const source = name();
    return {
      queryKey: hubConfigRecordKey(NOUN, source),
      enabled: source !== null,
      queryFn: async (): Promise<WorkSourceSummary> => {
        const { data, error } = await getWorkSourceApiWorkSourcesSourceGet({
          path: { source: source! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
    };
  });
}

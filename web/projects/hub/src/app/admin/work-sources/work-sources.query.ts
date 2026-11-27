import { injectQuery } from '@tanstack/angular-query-experimental';

import { getWorkSourceApiWorkSourcesSourceGet, listWorkSourcesApiWorkSourcesGet } from 'fleet/hub-api';
import { hubConfigListKey, hubConfigRecordKey } from '../../core/query-keys';
import type { WorkSource } from './work-source.domain';
import { fromWorkSourceSummary } from './work-source.translate';

const NOUN = 'work-sources';

/** `GET /api/work-sources` — every work source, retired ones too while
 * `includeRetired()` holds. Freshness: `hubConfigKey` (`web/projects/hub/src/app/core/query-keys.ts`). */
function workSourcesOptions(retired: boolean) {
  return {
    queryKey: hubConfigListKey(NOUN, retired),
    queryFn: async (): Promise<WorkSource[]> => {
      const { data, error } = await listWorkSourcesApiWorkSourcesGet({
        query: { include_retired: retired },
        throwOnError: false,
      });
      if (error) throw error;
      return (data?.sources ?? []).map(fromWorkSourceSummary);
    },
  };
}

export function injectWorkSourcesQuery(includeRetired: () => boolean) {
  return injectQuery(() => workSourcesOptions(includeRetired()));
}

/** `GET /api/work-sources/{source}` — one work source by its id or slug, at rest while `key()` is `null`. */
export function injectWorkSourceQuery(key: () => string | null) {
  return injectQuery(() => {
    const source = key();
    return {
      queryKey: hubConfigRecordKey(NOUN, source),
      enabled: source !== null,
      queryFn: async (): Promise<WorkSource> => {
        const { data, error } = await getWorkSourceApiWorkSourcesSourceGet({
          path: { source: source! },
          throwOnError: false,
        });
        if (error) throw error;
        return fromWorkSourceSummary(data!);
      },
    };
  });
}

/** Project projection over the shared domain cache. */
export function injectProjectWorkSourcesQuery(includeRetired: () => boolean) {
  return injectQuery(() => ({
    ...workSourcesOptions(includeRetired()),
    select: (rows: WorkSource[]) => rows.map(source => ({
      work_source_id: source.id, slug: source.slug, name: source.name, provider: source.provider,
      locator: source.locator, retired: source.retired, built_in: source.builtIn,
    })),
  }));
}

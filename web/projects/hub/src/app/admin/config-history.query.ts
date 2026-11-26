import { toConfigChange, toWireRecordKind } from './config-history.translate';
import { injectQuery } from '@tanstack/angular-query-experimental';

import { type ConfigChangeView, listChangesApiConfigChangesGet } from 'fleet/hub-api';
import type { ConfigRecordKind } from './config-history.domain';
import { hubConfigHistoryKey } from '../core/query-keys';

/** The page size each history request asks for. */
const HISTORY_PAGE = 100;

/**
 * One record's change history — `GET /api/config/changes` filtered to `recordKind` +
 * `recordKey()`, drained page by page through `next_before`, newest first. At rest
 * while `recordKey()` is `null`.
 */
function ConfigHistoryQueryOptions(
  recordKind: ConfigRecordKind,
  recordKey: () => string | null,
) {
  const key = recordKey();
  return {
    queryKey: hubConfigHistoryKey(recordKind, key),
    enabled: key !== null,
    queryFn: async (): Promise<ConfigChangeView[]> => {
      const rows: ConfigChangeView[] = [];
      let before: number | undefined;
      for (;;) {
        const { data, error } = await listChangesApiConfigChangesGet({
          query: {
            record_kind: toWireRecordKind(recordKind),
            record_key: key,
            limit: HISTORY_PAGE,
            before,
          },
          throwOnError: false,
        });
        if (error) throw error;
        rows.push(...(data?.changes ?? []));
        before = data?.next_before ?? undefined;
        if (before === undefined) return rows;
      }
    },
  };
}

export function injectConfigHistoryQuery(
  recordKind: ConfigRecordKind,
  recordKey: () => string | null,
) {
  return injectQuery(() => ({
    ...ConfigHistoryQueryOptions(recordKind, recordKey),
    select: (rows: ConfigChangeView[]) => rows.map(toConfigChange),
  }));
}

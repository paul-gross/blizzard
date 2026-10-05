import { injectQuery } from '@tanstack/angular-query-experimental';

import { type ConfigChangeView, hubConfigHistoryKey, listChangesApiConfigChangesGet, type RecordKind } from 'fleet';

/** The page size each history request asks for. */
const HISTORY_PAGE = 100;

/**
 * One record's change history — `GET /api/config/changes` filtered to `recordKind` +
 * `recordKey()`, drained page by page through `next_before`, newest first. The
 * newest row is who last changed the record and through which door; the rows are
 * its Revisions list. At rest while `recordKey()` is `null` (nothing selected, or a
 * built-in record).
 */
export function injectConfigHistoryQuery(recordKind: RecordKind, recordKey: () => string | null) {
  return injectQuery(() => {
    const key = recordKey();
    return {
      queryKey: hubConfigHistoryKey(recordKind, key),
      enabled: key !== null,
      queryFn: async (): Promise<ConfigChangeView[]> => {
        const rows: ConfigChangeView[] = [];
        let before: number | undefined;
        for (;;) {
          const { data, error } = await listChangesApiConfigChangesGet({
            query: { record_kind: recordKind, record_key: key, limit: HISTORY_PAGE, before },
            throwOnError: false,
          });
          if (error) throw error;
          rows.push(...(data?.changes ?? []));
          before = data?.next_before ?? undefined;
          if (before === undefined) return rows;
        }
      },
    };
  });
}

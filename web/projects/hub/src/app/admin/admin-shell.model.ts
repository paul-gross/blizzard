import type { ConfigChangePage } from './config-history.domain';

/** How many records an admin tab holds, `null` while its read has not answered (or is not the actor's to make). */
export function tabCount(records: readonly unknown[] | undefined): number | null {
  if (!Array.isArray(records)) return null;
  return records.length;
}

/** Count records selected by the same keys as their lensed list. */
export function linkedTabCount<T>(
  records: readonly T[] | undefined,
  keyOf: (record: T) => string,
  keys: ReadonlySet<string> | null,
): number | null {
  if (records === undefined) return null;
  return keys === null ? records.length : records.filter((record) => keys.has(keyOf(record))).length;
}

/** How many changes the change log's filters select — its first page's `total` — `null` until that page answers. */
export function changesTotal(pages: readonly ConfigChangePage[] | undefined): number | null {
  return pages?.[0]?.total ?? null;
}

/** Count live urgencies once the shared read answers. */
export function liveUrgencyCount(records: readonly { readonly retired: boolean }[] | undefined): number | null {
  return tabCount(records?.filter((record) => !record.retired));
}

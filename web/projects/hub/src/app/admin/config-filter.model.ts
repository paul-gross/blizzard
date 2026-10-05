import type { KitChipOption } from 'fleet';

/** Which records a config list shows — the board's own filter over the wire's
 * `retired` flag, not a backend vocabulary. */
export type LifecycleFilter = 'active' | 'retired' | 'all';

/** The URL query param the filter rides (`route-state.ts`). */
export const LIFECYCLE_FILTER_PARAM = 'status';

/** The filter chips, in the order the lists show them. */
export const LIFECYCLE_FILTER_OPTIONS: readonly KitChipOption[] = [
  { value: 'active', label: 'Active', testid: 'config-filter-active' },
  { value: 'retired', label: 'Retired', testid: 'config-filter-retired' },
  { value: 'all', label: 'All', testid: 'config-filter-all' },
];

/** The filter a URL param names — `active` when the param is absent or names no
 * filter, so a bare list URL lands on the live records. */
export function parseLifecycleFilter(param: string | null): LifecycleFilter {
  return param === 'retired' || param === 'all' ? param : 'active';
}

/** The URL param value for `filter` — `null` for the default, so the default list
 * keeps a bare URL. */
export function lifecycleFilterParam(filter: string): string | null {
  const parsed = parseLifecycleFilter(filter);
  return parsed === 'active' ? null : parsed;
}

/** Whether the list read must ask the hub for retired records too. */
export function includeRetired(filter: LifecycleFilter): boolean {
  return filter !== 'active';
}

/** The records `filter` shows, by each record's own `retired` flag. */
export function filterByLifecycle<T extends { readonly retired?: boolean }>(
  records: readonly T[],
  filter: LifecycleFilter,
): readonly T[] {
  if (filter === 'all') return records;
  const wantRetired = filter === 'retired';
  return records.filter((record) => (record.retired ?? false) === wantRetired);
}

/** The list's empty-state copy for `noun` under `filter`. */
export function lifecycleEmptyText(noun: string, filter: LifecycleFilter): string {
  if (filter === 'retired') return `No retired ${noun}.`;
  if (filter === 'all') return `No ${noun} yet.`;
  return `No active ${noun}.`;
}

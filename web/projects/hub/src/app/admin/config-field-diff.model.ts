import type { FieldChangeView } from 'fleet';

/** One rendered row of a field diff. */
export interface FieldDiffRowVm {
  readonly field: string;
  readonly old: string;
  readonly new: string;
}

/** A diff value as text — `—` for an unset value, strings as they are, anything
 * else as its JSON. */
export function formatDiffValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

/** The rows a field diff renders. */
export function fieldDiffRows(diff: readonly FieldChangeView[]): readonly FieldDiffRowVm[] {
  return diff.map((change) => ({ field: change.field, old: formatDiffValue(change.old), new: formatDiffValue(change.new) }));
}

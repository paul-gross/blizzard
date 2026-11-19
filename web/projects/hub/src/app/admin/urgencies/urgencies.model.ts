import type { KitChipOption, Urgency } from 'fleet';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';

/** Keep the hub's live ordering; retired records follow the live set. */
export function visibleUrgencies(records: readonly Urgency[], filter: LifecycleFilter): readonly Urgency[] {
  return filterByLifecycle(records, filter);
}

import type { UrgencyMove } from './urgency.domain';
import { movedItem } from '../../core/array-move';
import { urgencyOptions as buildUrgencyOptions } from '../../core/urgency-options.model';

export function urgencyPosition(rows: readonly Urgency[], from: number, to: number): UrgencyMove | null {
  if (from === to || from < 0 || to < 0 || from >= rows.length || to >= rows.length) return null;
  const ordered = movedItem(rows, from, to);
  return { urgency: ordered[to].id, after: to === 0 ? null : ordered[to - 1].id };
}

export function overlayUrgencies(records: readonly Urgency[], moves: readonly UrgencyMove[], defaults: readonly { key: string }[]): readonly Urgency[] {
  let rows = [...records];
  for (const move of moves) {
    const item = rows.find((row) => row.id === move.urgency);
    if (!item || (move.after && !rows.some((row) => row.id === move.after))) continue;
    rows = rows.filter((row) => row.id !== move.urgency);
    const index = move.after ? rows.findIndex((row) => row.id === move.after) + 1 : 0;
    rows.splice(index, 0, item);
  }
  const selected = defaults.at(-1)?.key;
  return selected ? rows.map((row) => ({ ...row, default: row.id === selected })) : rows;
}

export function liveUrgencies(records: readonly Urgency[]): readonly Urgency[] {
  return records.filter((record) => !record.retired);
}

export function urgencyOptions(records: readonly Urgency[]): readonly KitChipOption[] {
  return buildUrgencyOptions(records, { key: 'id', retired: 'include' });
}

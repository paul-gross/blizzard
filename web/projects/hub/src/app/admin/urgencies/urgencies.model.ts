import type { UrgencyView } from 'fleet';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';

/** Keep the hub's live ordering; retired records follow the live set. */
export function visibleUrgencies(records: readonly UrgencyView[], filter: LifecycleFilter): readonly UrgencyView[] {
  return filterByLifecycle(records, filter);
}

import { ProjectColor, type UrgencyCreateRequest, type UrgencyPatchRequest, type UrgencyPositionRequest } from 'fleet';
import { movedItem } from '../../core/array-move';
import { type ConfigFieldDef, type ConfigFormValues, markIsImage } from '../config-edit.model';

export const URGENCY_FIELDS: readonly ConfigFieldDef[] = [
  { key: 'slug', label: 'Slug', kind: 'text', required: true, createOnly: true },
  { key: 'name', label: 'Name', kind: 'text', required: true },
  { key: 'color', label: 'Color', kind: 'color', options: Object.values(ProjectColor).map((value) => ({ value, label: value })) },
  { key: 'mark', label: 'Mark', kind: 'mark' },
];

function text(values: ConfigFormValues, key: string): string {
  const value = values[key];
  return typeof value === 'string' ? value.trim() : '';
}
function icon(value: string): { media_type: string; data: string } {
  const [header, data] = value.split(',');
  return { media_type: header.slice(5, header.indexOf(';')), data };
}
export function urgencyCreate(values: ConfigFormValues): UrgencyCreateRequest {
  const mark = text(values, 'mark');
  const color = text(values, 'color');
  return { slug: text(values, 'slug'), name: text(values, 'name'), ...(color ? { color } : {}),
    ...(mark ? markIsImage(mark) ? { icon: icon(mark) } : { emoji: mark } : {}) };
}
export function urgencyEdit(shown: ConfigFormValues, values: ConfigFormValues): UrgencyPatchRequest {
  const body: UrgencyPatchRequest = {};
  for (const key of ['name', 'color'] as const) {
    if (text(shown, key) !== text(values, key)) body[key] = text(values, key);
  }
  const before = text(shown, 'mark');
  const mark = text(values, 'mark');
  if (before !== mark) {
    body.emoji = mark && !markIsImage(mark) ? mark : null;
    body.icon = mark && markIsImage(mark) ? icon(mark) : null;
  }
  return body;
}
export function urgencyPosition(rows: readonly UrgencyView[], from: number, to: number): UrgencyPositionRequest | null {
  if (from === to || from < 0 || to < 0 || from >= rows.length || to >= rows.length) return null;
  const ordered = movedItem(rows, from, to);
  return { urgency: ordered[to].urgency_id, after: to === 0 ? null : ordered[to - 1].urgency_id };
}

export function overlayUrgencies(records: readonly UrgencyView[], moves: readonly UrgencyPositionRequest[], defaults: readonly { key: string }[]): readonly UrgencyView[] {
  let rows = [...records];
  for (const move of moves) {
    const item = rows.find((row) => row.urgency_id === move.urgency);
    if (!item || (move.after && !rows.some((row) => row.urgency_id === move.after))) continue;
    rows = rows.filter((row) => row.urgency_id !== move.urgency);
    const index = move.after ? rows.findIndex((row) => row.urgency_id === move.after) + 1 : 0;
    rows.splice(index, 0, item);
  }
  const selected = defaults.at(-1)?.key;
  return selected ? rows.map((row) => ({ ...row, default: row.urgency_id === selected })) : rows;
}

export function liveUrgencies(records: readonly UrgencyView[]): readonly UrgencyView[] {
  return records.filter((record) => !record.retired);
}

export function urgencyOptions(records: readonly UrgencyView[]): readonly { value: string; label: string }[] {
  return records.map((record) => ({ value: record.urgency_id, label: record.name }));
}

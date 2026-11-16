import type { FieldChangeView, HubSettingRow, HubSettingsChange } from 'fleet';
import type { ConfigRecordVm } from '../config-record-panel';

export function display(value: unknown): string { return typeof value === 'string' ? value : JSON.stringify(value); }
export function changesOf(pages: readonly { changes: HubSettingsChange[] }[] | undefined): HubSettingsChange[] {
  return pages?.flatMap((page) => page.changes) ?? [];
}
export function revisionsOf(changes: readonly HubSettingsChange[]) {
  return changes.map((c) => ({ id: c.id, revision: c.revision, op: c.op, fields: '', at: c.recorded_at, actor: c.actor, door: c.door }));
}
export function recordOf(row: HubSettingRow): ConfigRecordVm {
  return { name: row.key, badges: [], facts: [{ label: 'Value', value: display(row.value) }, { label: 'Source', value: row.source }, { label: 'Lifetime', value: row.lifetime }], revision: row.revision, note: null, links: null, hasHistory: false };
}
export function diffOf(change: HubSettingsChange): FieldChangeView[] {
  const value = (entry: unknown): unknown => entry && typeof entry === 'object' && 'value' in entry ? entry.value : entry;
  return change.diff.map((d) => ({ field: String(d['key']), old: value(d['old']), new: value(d['new']), old_label: null, new_label: null }));
}

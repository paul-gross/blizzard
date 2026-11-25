import { type HubSettingRow, type HubSettingsChange, settingValueText } from 'fleet';
import type { ConfigRecordVm } from '../config-record-panel';

export function changesOf(pages: readonly { changes: HubSettingsChange[] }[] | undefined): HubSettingsChange[] {
  return pages?.flatMap((page) => page.changes) ?? [];
}
export function revisionsOf(changes: readonly HubSettingsChange[]) {
  return changes.map((c) => ({ id: c.id, revision: c.revision, op: c.op, fields: '', at: c.recorded_at, actor: c.actor, door: c.door }));
}
export function recordOf(row: HubSettingRow): ConfigRecordVm {
  return { name: row.key, badges: [], facts: [{ label: 'Value', value: settingValueText(row.value) }, { label: 'Source', value: row.source }, { label: 'Lifetime', value: row.lifetime }], revision: row.revision, note: null, links: null, hasHistory: false };
}

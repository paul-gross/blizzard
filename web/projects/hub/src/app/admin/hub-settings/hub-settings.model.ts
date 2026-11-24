import { type HubSettingRow, type HubSettingsChange, type ParsedSettingValue, parseSettingText, settingValueText } from 'fleet';
import type { ConfigRecordVm } from '../config-record-panel';

export const display = settingValueText;
/** The value to send for an edit's text of `row`, read by `parseSettingText`. */
export function parseSettingValue(row: HubSettingRow, text: string): ParsedSettingValue {
  return parseSettingText(row.value, text);
}
export function changesOf(pages: readonly { changes: HubSettingsChange[] }[] | undefined): HubSettingsChange[] {
  return pages?.flatMap((page) => page.changes) ?? [];
}
export function revisionsOf(changes: readonly HubSettingsChange[]) {
  return changes.map((c) => ({ id: c.id, revision: c.revision, op: c.op, fields: '', at: c.recorded_at, actor: c.actor, door: c.door }));
}
export function recordOf(row: HubSettingRow): ConfigRecordVm {
  return { name: row.key, badges: [], facts: [{ label: 'Value', value: display(row.value) }, { label: 'Source', value: row.source }, { label: 'Lifetime', value: row.lifetime }], revision: row.revision, note: null, links: null, hasHistory: false };
}

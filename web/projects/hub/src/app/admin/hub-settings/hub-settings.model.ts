import type { HubSettingRow, HubSettingsChange } from 'fleet';
import type { ConfigRecordVm } from '../config-record-panel';

export function display(value: unknown): string { return typeof value === 'string' ? value : JSON.stringify(value); }
export type ParsedSetting = { value: unknown } | { error: string };
/** The value to send for an edit's text: a string setting passes through; any other type is JSON. */
export function parseSettingValue(row: HubSettingRow, text: string): ParsedSetting {
  if (typeof row.value === 'string') return { value: text };
  try { return { value: JSON.parse(text) }; } catch { return { error: 'Enter a valid JSON value.' }; }
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

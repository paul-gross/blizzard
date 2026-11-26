import { settingValueText } from 'fleet';
import type { HubSettingRow, HubSettingsChange } from './hub-settings.domain';
import type { ChangeEntryVm } from '../changes/changes.model';
import type { ConfigFieldView } from '../config-field-tree.model';
import { hubSettingValueText } from './hub-settings.domain';
import type { ConfigRecordVm } from '../config-record-panel';
import type { HubSettingDraft } from './hub-settings.domain';

/** The edit dialog's one field: the value, or the key's default. */
export const HUB_SETTING_FIELD_VIEWS: readonly ConfigFieldView<HubSettingDraft>[] = [
  { key: 'value', kind: 'text', label: 'Value', orDefault: { key: 'useDefault', label: 'Default' } },
];

/** One change-entry panel per settings revision, each holding its single change. */
export function changeEntriesOf(changes: readonly HubSettingsChange[]): readonly ChangeEntryVm[] {
  return changes.map((c) => ({
    title: `Revision ${c.revision} · ${hubSettingValueText(c.op)}`,
    actor: c.actor,
    door: hubSettingValueText(c.door),
    at: c.recorded_at,
    rows: [{ id: c.id, title: 'hub settings', route: null, diff: c.diff, revisionStep: null }],
  }));
}

export function changesOf(pages: readonly { changes: readonly HubSettingsChange[] }[] | undefined): HubSettingsChange[] {
  return pages?.flatMap((page) => page.changes) ?? [];
}
export function revisionsOf(changes: readonly HubSettingsChange[]) {
  return changes.map((c) => ({ id: c.id, revision: c.revision, op: hubSettingValueText(c.op), fields: '', at: c.recorded_at, actor: c.actor, door: hubSettingValueText(c.door) }));
}
export function recordOf(row: HubSettingRow): ConfigRecordVm {
  return { name: row.key, badges: [], facts: [{ label: 'Value', value: settingValueText(row.value) }, { label: 'Source', value: hubSettingValueText(row.source) }, { label: 'Lifetime', value: hubSettingValueText(row.lifetime) }], revision: row.revision, note: null, links: null, hasHistory: false };
}

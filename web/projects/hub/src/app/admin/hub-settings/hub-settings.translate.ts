// bzh:frontend-domain-boundary — the hub settings wire read into the edit's domain.
import { readRefusal, type RefusalMap, type SaveOutcome, settingValueText } from 'fleet';

import type { HubSettingBaseline, HubSettingDraft, HubSettingRow } from './hub-settings.domain';

/** What an edit of `row` starts from: the key, the settings read's revision, and the key's value, all from one read. */
export function toHubSettingBaseline(revision: number, row: HubSettingRow): HubSettingBaseline {
  return {
    id: row.key,
    revision,
    draft: { value: settingValueText(row.value), useDefault: row.source === 'default' },
    current: row.value,
  };
}

const HUB_SETTING_REFUSALS: RefusalMap<HubSettingDraft> = { field: () => null, kinds: {} };

/** A refused write as a {@link SaveOutcome}, on the form line and worded by its `detail`: the hub names no setting in
 * a refusal, so none can be placed on a field. */
export function hubSettingRefusal(error: unknown): SaveOutcome<HubSettingDraft> {
  return readRefusal(error, HUB_SETTING_REFUSALS, 'Save failed.');
}

import type { HubSettingRow as WireHubSettingRow, HubSettingsChange } from 'fleet';
import type { HubSettings, HubSettingsHistoryPage } from './hub-settings.domain';

export function toHubSettings(view: { revision: number; settings: WireHubSettingRow[] }): HubSettings {
  return { revision: view.revision, settings: view.settings.map((row) => ({ ...row })) };
}

export function toHubSettingsHistoryPage(page: { changes: HubSettingsChange[]; next_before: number | null }): HubSettingsHistoryPage {
  return {
    next_before: page.next_before,
    changes: page.changes.map((change) => ({ ...change, diff: change.diff.map((field) => ({ ...field })) })),
  };
}

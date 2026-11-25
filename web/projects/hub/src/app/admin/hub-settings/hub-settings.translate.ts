// bzh:frontend-domain-boundary — the hub settings wire read into the edit's domain.
import { type HubSettingRow, readRefusal, type RefusalMap, type SaveOutcome, settingValueText } from 'fleet';

import type { HubSettingBaseline, HubSettingDraft } from './hub-settings.domain';

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

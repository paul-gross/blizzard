// bzh:frontend-domain-boundary — the hub settings wire read into the edit's domain.
import { settingValueText, openLookup, readRefusal } from 'fleet/core';
import { type RefusalMap, type SaveOutcome } from 'fleet/forms';
import { type hubApi, type HubSettingRow as WireHubSettingRow, type HubSettingsChange } from 'fleet/hub-api';

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

import type { HubSettingLifetime, HubSettingSource, HubSettings, HubSettingsDoor, HubSettingsHistoryPage, HubSettingsOperation, UnknownHubSettingValue } from './hub-settings.domain';

const unknown = (raw: string): UnknownHubSettingValue => ({ kind: 'unknown', raw });
const lifetime = openLookup<hubApi.Lifetime, HubSettingLifetime>(
  { pass: 'pass', session: 'session', boot: 'boot', immutable: 'immutable' }, unknown,
);
const source = openLookup<WireHubSettingRow['source'], HubSettingSource>({ entry: 'entry', default: 'default' }, unknown);
const operation = openLookup<hubApi.Op, HubSettingsOperation>(
  { seed: 'seed', edit: 'edit', create: 'create', retire: 'retire', enable: 'enable' }, unknown,
);
const door = openLookup<hubApi.Door, HubSettingsDoor>(
  { seed: 'seed', cli: 'cli', page: 'page', api: 'api', apply: 'apply' }, unknown,
);

export function toHubSettings(view: { revision: number; settings: WireHubSettingRow[] }): HubSettings {
  return { revision: view.revision, settings: view.settings.map((row) => ({ ...row, lifetime: lifetime(row.lifetime), source: source(row.source) })) };
}

export function toHubSettingsHistoryPage(page: { changes: HubSettingsChange[]; next_before: number | null }): HubSettingsHistoryPage {
  return {
    next_before: page.next_before,
    changes: page.changes.map((change) => ({ ...change, op: operation(change.op), door: door(change.door), diff: change.diff.map((field) => ({ ...field })) })),
  };
}

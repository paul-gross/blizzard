import type { SettingRow, SettingsRecord } from './settings.domain';

/** `throttle.budget.usd` at its default, a `pass` key. */
export function settingRow(overrides: Partial<SettingRow> = {}): SettingRow {
  return {
    key: 'throttle.budget.usd',
    value: null,
    source: 'default',
    lifetime: 'pass',
    revision: null,
    writtenAt: null,
    writtenBy: null,
    ...overrides,
  };
}

/** A settings record at revision 3, revision 3 in force, holding one key. */
export function settingsRecord(overrides: Partial<SettingsRecord> = {}): SettingsRecord {
  return {
    revision: 3,
    inForce: { revision: 3, since: '2026-11-20T21:14:00Z', failed: null },
    settings: [settingRow()],
    settingsError: null,
    ...overrides,
  };
}

import type { ChangeView, ChangesPage, HostChangeView, HostChangesPage, SettingRow, SettingsView } from '../../api/runner';
import { fixtureInstant } from './clock';

/** `throttle.budget.usd` at its default, a `pass` key. */
export function runnerSettingRowView(overrides: Partial<SettingRow> = {}): SettingRow {
  return {
    key: 'throttle.budget.usd',
    value: null,
    source: 'default',
    lifetime: 'pass',
    revision: null,
    written_at: null,
    written_by: null,
    ...overrides,
  };
}

/** A settings record at revision 3, revision 3 in force for the last hour, holding one key. */
export function runnerSettingsView(overrides: Partial<SettingsView> = {}): SettingsView {
  return {
    revision: 3,
    in_force: { revision: 3, since: fixtureInstant(-3600), failed: null },
    settings: [runnerSettingRowView()],
    settings_error: null,
    ...overrides,
  };
}

/** Revision 3 of a runner's record: `throttle.budget.usd` set from its default to 25 through the api. */
export function runnerSettingsChangeView(overrides: Partial<ChangeView> = {}): ChangeView {
  return {
    id: 3,
    recorded_at: fixtureInstant(-600),
    revision: 3,
    op: 'edit',
    door: 'api',
    actor: 'paul',
    apply_id: null,
    diff: [{ key: 'throttle.budget.usd', old: { value: null, at_default: true }, new: { value: 25, at_default: false } }],
    ...overrides,
  };
}

/** One page of a runner's change log, the oldest. */
export function runnerSettingsChangesPage(overrides: Partial<ChangesPage> = {}): ChangesPage {
  return { changes: [runnerSettingsChangeView()], next_before: null, ...overrides };
}

/** Revision 2 of the host's own record: `max_agents` set to 6 through the cli. */
export function hostSettingsChangeView(overrides: Partial<HostChangeView> = {}): HostChangeView {
  return {
    id: 2,
    recorded_at: fixtureInstant(-1200),
    revision: 2,
    op: 'edit',
    door: 'cli',
    actor: 'paul',
    apply_id: null,
    record_kind: 'host',
    record_key: null,
    diff: [{ key: 'max_agents', old: { value: 4, at_default: false }, new: { value: 6, at_default: false } }],
    ...overrides,
  };
}

/** One page of the host's change log, the oldest. */
export function hostSettingsChangesPage(overrides: Partial<HostChangesPage> = {}): HostChangesPage {
  return { changes: [hostSettingsChangeView()], next_before: null, ...overrides };
}

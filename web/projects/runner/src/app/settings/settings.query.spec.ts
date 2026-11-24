import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet';
import { runnerSettingsView } from 'fleet/testing';

import { SETTINGS_PENDING_POLL_MS, settingsChangesKeyOf, settingsKeyOf, settingsQueryOptions } from './settings.query';

describe('settings query', () => {
  const interval = (data?: ReturnType<typeof runnerSettingsView>) =>
    settingsQueryOptions({ kind: 'runner', runnerId: 'rn_1' }).refetchInterval({ state: { data } });

  it('polls while a written revision waits to take hold, and rides the backstop otherwise', () => {
    expect(interval(undefined)).toBe(RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS);
    expect(interval(runnerSettingsView())).toBe(RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS);
    expect(interval(runnerSettingsView({ revision: 4 }))).toBe(SETTINGS_PENDING_POLL_MS);
  });

  it('keys the host`s record and log under the host, a runner`s under its id', () => {
    expect(settingsKeyOf({ kind: 'host' })).toEqual(['host', 'settings']);
    expect(settingsChangesKeyOf({ kind: 'host' })).toEqual(['host', 'settings', 'changes']);
    expect(settingsKeyOf({ kind: 'runner', runnerId: 'rn_1' })).toEqual(['rn_1', 'settings']);
    expect(settingsChangesKeyOf({ kind: 'runner', runnerId: 'rn_1' })).toEqual(['rn_1', 'settings', 'changes']);
  });
});

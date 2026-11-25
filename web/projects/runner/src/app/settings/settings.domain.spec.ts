import { settingRow, settingsRecord } from './settings.builders';
import { canEditSetting, revisionPending } from './settings.domain';

describe('settings domain', () => {
  it('offers an edit only to the superuser, on a pass or session key, of a record not retired', () => {
    expect(canEditSetting(true, settingRow({ lifetime: 'pass' }), false)).toBe(true);
    expect(canEditSetting(true, settingRow({ lifetime: 'session' }), false)).toBe(true);
    expect(canEditSetting(false, settingRow({ lifetime: 'pass' }), false)).toBe(false);
    expect(canEditSetting(true, settingRow({ lifetime: 'pass' }), true)).toBe(false);
    expect(canEditSetting(true, settingRow({ lifetime: 'immutable' }), false)).toBe(false);
    expect(canEditSetting(true, settingRow({ lifetime: 'boot' }), false)).toBe(false);
    expect(canEditSetting(true, settingRow({ lifetime: { kind: 'unknown', raw: 'tick' } }), false)).toBe(false);
  });

  it('reads a written revision pending while the one in force is older or none is in force', () => {
    expect(revisionPending(settingsRecord())).toBe(false);
    expect(revisionPending(settingsRecord({ revision: 4 }))).toBe(true);
    expect(
      revisionPending(
        settingsRecord({ revision: 4, inForce: { revision: 3, since: null, failed: { revision: 4, error: 'x', since: '' } } }),
      ),
    ).toBe(true);
    expect(revisionPending(settingsRecord({ inForce: { revision: null, since: null, failed: null } }))).toBe(true);
    expect(revisionPending(settingsRecord({ revision: 0, inForce: { revision: null, since: null, failed: null } }))).toBe(false);
  });
});

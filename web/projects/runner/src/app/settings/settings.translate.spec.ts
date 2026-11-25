import { hostSettingsChangesPage, hostSettingsChangeView, runnerSettingRowView, runnerSettingsChangesPage, runnerSettingsView } from 'fleet/testing';

import { settingRow, settingsRecord } from './settings.builders';
import {
  fromChangesPage,
  fromHostChangesPage,
  fromSettingsView,
  settingRefusal,
  toSettingBaseline,
  toSettingsWrite,
} from './settings.translate';

function problem(overrides: Record<string, unknown>) {
  return {
    type: 'urn:blizzard:problem:x',
    title: 'x',
    status: 422,
    detail: 'refused',
    code: 'setting_unknown',
    key: null,
    current: null,
    errors: null,
    record_kind: null,
    record_key: null,
    ...overrides,
  };
}

describe('settings translate', () => {
  it('reads a settings view, its in_force and failed rebuild included', () => {
    const record = fromSettingsView(
      runnerSettingsView({
        revision: 5,
        in_force: { revision: 4, since: '2026-01-01T00:00:00Z', failed: { revision: 5, error: 'no file', since: '2026-01-01T00:01:00Z' } },
        settings: [runnerSettingRowView({ source: 'entry', revision: 5, written_at: '2026-01-01T00:00:30Z', written_by: 'paul' })],
      }),
    );

    expect(record).toEqual({
      revision: 5,
      inForce: { revision: 4, since: '2026-01-01T00:00:00Z', failed: { revision: 5, error: 'no file', since: '2026-01-01T00:01:00Z' } },
      settings: [
        settingRow({ source: 'entry', revision: 5, writtenAt: '2026-01-01T00:00:30Z', writtenBy: 'paul' }),
      ],
      settingsError: null,
    });
  });

  it('reads a lifetime or source this build does not know as its raw value', () => {
    const record = fromSettingsView(
      runnerSettingsView({ settings: [runnerSettingRowView({ lifetime: 'tick' as never, source: 'file' as never })] }),
    );

    expect(record.settings[0].lifetime).toEqual({ kind: 'unknown', raw: 'tick' });
    expect(record.settings[0].source).toEqual({ kind: 'unknown', raw: 'file' });
  });

  it('reads a runner change page with no record, and a host page naming each row`s record', () => {
    const runner = fromChangesPage(runnerSettingsChangesPage({ next_before: 3 }));
    expect(runner.nextBefore).toBe(3);
    expect(runner.changes[0]).toMatchObject({ revision: 3, op: 'edit', door: 'api', record: null });
    expect(runner.changes[0].diff).toEqual([
      { key: 'throttle.budget.usd', old: { value: null, atDefault: true }, new: { value: 25, atDefault: false } },
    ]);

    const host = fromHostChangesPage(
      hostSettingsChangesPage({
        changes: [hostSettingsChangeView({ record_kind: 'workspace', record_key: 'ws-a', op: 'mint', door: 'page' })],
      }),
    );
    expect(host.changes[0]).toMatchObject({
      op: { kind: 'unknown', raw: 'mint' },
      door: 'page',
      record: { kind: 'workspace', key: 'ws-a' },
    });
  });

  it('captures the record id, its revision, and the key`s text from one read', () => {
    const record = settingsRecord({ revision: 7 });
    expect(toSettingBaseline('rn_1', record, settingRow({ value: 25 }))).toEqual({ id: 'rn_1', revision: 7, draft: { value: '25' } });
    expect(toSettingBaseline('host', record, settingRow({ value: 'x y' })).draft.value).toBe('x y');
  });

  it('writes a set or an unset of one key', () => {
    expect(toSettingsWrite({ kind: 'set', key: 'a.b', value: 3 })).toEqual({ set: { 'a.b': 3 } });
    expect(toSettingsWrite({ kind: 'unset', key: 'a.b' })).toEqual({ unset: ['a.b'] });
  });

  it('answers a revision conflict on the form line', () => {
    expect(settingRefusal(problem({ status: 409, code: 'revision_conflict', detail: 'moved', current: 8 }), 'a')).toEqual({
      ok: false,
      fields: {},
      form: 'moved',
    });
  });

  it('answers a refusal of the key beside its value, and another key`s on the form line', () => {
    expect(settingRefusal(problem({ code: 'setting_not_live', key: 'a', detail: 'needs a restart' }), 'a')).toEqual({
      ok: false,
      fields: { value: 'needs a restart' },
      form: null,
    });
    expect(settingRefusal(problem({ key: 'b', detail: 'unknown b' }), 'a')).toEqual({ ok: false, fields: {}, form: 'unknown b' });
    expect(
      settingRefusal(
        problem({ code: 'settings_invalid', errors: [{ key: 'a', message: 'must be positive' }, { key: 'c', message: 'too low' }] }),
        'a',
      ),
    ).toEqual({ ok: false, fields: { value: 'must be positive' }, form: 'c: too low' });
  });

  it('reads a request validation error or a failure with no problem through readRefusal', () => {
    expect(settingRefusal({ detail: [{ loc: ['body', 'set'], msg: 'bad' }] }, 'a')).toEqual({
      ok: false,
      fields: { value: 'bad' },
      form: null,
    });
    expect(settingRefusal(new TypeError('Failed to fetch'), 'a')).toEqual({ ok: false, fields: {}, form: 'Save failed.' });
  });
});

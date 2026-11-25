import type { HubSettingRow } from './hub-settings.domain';

import { hubSettingRefusal, toHubSettingBaseline } from './hub-settings.translate';

const ROW: HubSettingRow = { key: 'alpha', value: 60, lifetime: 'pass', source: 'entry', revision: 3, written_at: null, written_by: null };

describe('toHubSettingBaseline', () => {
  it('captures the key, the read revision, the value as text, and the value itself', () => {
    expect(toHubSettingBaseline(7, ROW)).toEqual({ id: 'alpha', revision: 7, draft: { value: '60', useDefault: false }, current: 60 });
  });

  it('opens a key at its default with the default chosen', () => {
    expect(toHubSettingBaseline(7, { ...ROW, source: 'default' }).draft.useDefault).toBe(true);
  });
});

describe('hubSettingRefusal', () => {
  it("puts the hub's settings-invalid body on the form line", () => {
    const message = "produces_mode must be one of ['enforce', 'warn'], got 'bogus'";
    const body = {
      type: 'urn:blizzard:problem:settings-invalid',
      code: 'settings_invalid',
      detail: `config: ${message}`,
      errors: { config: message },
    };
    expect(hubSettingRefusal(body)).toEqual({ ok: false, fields: {}, form: `config: ${message}` });
  });

  it('puts anything else on the form line, falling back to Save failed.', () => {
    expect(hubSettingRefusal({ type: 'urn:blizzard:problem:setting-unknown', detail: 'unknown key', errors: {} })).toEqual({
      ok: false, fields: {}, form: 'unknown key',
    });
    expect(hubSettingRefusal(new Error('network'))).toEqual({ ok: false, fields: {}, form: 'Save failed.' });
  });
});

import { toHubSettings, toHubSettingsHistoryPage } from './hub-settings.translate';

it('preserves settings and history fields including cursors and field values', () => {
  const settings = { revision: 7, settings: [{ key: 'key', value: { a: 1 }, lifetime: 'pass' as const, source: 'default' as const, revision: null, written_at: null, written_by: null }] };
  expect(toHubSettings(settings)).toEqual(settings);
  const page = { changes: [{ id: 2, revision: 7, op: 'edit' as const, door: 'api' as const, actor: 'a', recorded_at: 'now', apply_id: null,
    diff: [{ field: 'key', old: null, new: { a: 1 }, old_label: null, new_label: 'new' }] }], next_before: 2 };
  expect(toHubSettingsHistoryPage(page)).toEqual(page);
});

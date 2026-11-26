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
import type { hubApi, HubSettingRow as WireHubSettingRow, HubSettingsChange } from 'fleet';

it('preserves settings and history fields including cursors and field values', () => {
  const settings = { revision: 7, settings: [{ key: 'key', value: { a: 1 }, lifetime: 'pass' as const, source: 'default' as const, revision: null, written_at: null, written_by: null }] };
  expect(toHubSettings(settings)).toEqual(settings);
  const page = { changes: [{ id: 2, revision: 7, op: 'edit' as const, door: 'api' as const, actor: 'a', recorded_at: 'now', apply_id: null,
    diff: [{ field: 'key', old: null, new: { a: 1 }, old_label: null, new_label: 'new' }] }], next_before: 2 };
  expect(toHubSettingsHistoryPage(page)).toEqual(page);
});

it('translates every known vocabulary and retains unforeseen spellings', () => {
  const row: WireHubSettingRow = { key: 'key', value: 1, lifetime: 'pass', source: 'default', revision: null, written_at: null, written_by: null };
  const change: HubSettingsChange = { id: 1, revision: 1, op: 'edit', door: 'api', actor: 'a', recorded_at: 'now', apply_id: null, diff: [] };
  for (const lifetime of ['pass', 'session', 'boot', 'immutable'] satisfies hubApi.Lifetime[]) {
    expect(toHubSettings({ revision: 1, settings: [{ ...row, lifetime }] }).settings[0].lifetime).toBe(lifetime);
  }
  for (const source of ['entry', 'default'] satisfies WireHubSettingRow['source'][]) {
    expect(toHubSettings({ revision: 1, settings: [{ ...row, source }] }).settings[0].source).toBe(source);
  }
  for (const op of ['seed', 'edit', 'create', 'retire', 'enable'] satisfies hubApi.Op[]) {
    expect(toHubSettingsHistoryPage({ changes: [{ ...change, op }], next_before: null }).changes[0].op).toBe(op);
  }
  for (const door of ['seed', 'cli', 'page', 'api', 'apply'] satisfies hubApi.Door[]) {
    expect(toHubSettingsHistoryPage({ changes: [{ ...change, door }], next_before: null }).changes[0].door).toBe(door);
  }
  const settings = toHubSettings({ revision: 1, settings: [{ ...row, lifetime: 'future-lifetime' as hubApi.Lifetime, source: 'future-source' as WireHubSettingRow['source'] }] });
  expect(settings.settings[0]).toMatchObject({ lifetime: { kind: 'unknown', raw: 'future-lifetime' }, source: { kind: 'unknown', raw: 'future-source' } });
  const history = toHubSettingsHistoryPage({ changes: [{ ...change, op: 'future-op' as hubApi.Op, door: 'future-door' as hubApi.Door }], next_before: null });
  expect(history.changes[0]).toMatchObject({ op: { kind: 'unknown', raw: 'future-op' }, door: { kind: 'unknown', raw: 'future-door' } });
});

import type { HubSettingRow } from 'fleet';

import { hubSettingRefusal, toHubSettingBaseline } from './hub-settings.translate';

const ROW: HubSettingRow = { key: 'alpha', value: 60, lifetime: 'pass', source: 'entry', revision: 3, written_at: null, written_by: null };
const invalid = (errors: Record<string, string>) => ({ type: 'urn:blizzard:problem:settings-invalid', detail: 'refused', errors });

describe('toHubSettingBaseline', () => {
  it('captures the key, the read revision, the value as text, and the value itself', () => {
    expect(toHubSettingBaseline(7, ROW)).toEqual({ id: 'alpha', revision: 7, draft: { value: '60', useDefault: false }, current: 60 });
  });

  it('opens a key at its default with the default chosen', () => {
    expect(toHubSettingBaseline(7, { ...ROW, source: 'default' }).draft.useDefault).toBe(true);
  });
});

describe('hubSettingRefusal', () => {
  it("puts a settings-invalid entry for the key on value, and another key's on the form line", () => {
    expect(hubSettingRefusal(invalid({ alpha: 'too small' }), 'alpha', false)).toEqual({ ok: false, fields: { value: 'too small' }, form: null });
    expect(hubSettingRefusal(invalid({ alpha: 'too small', beta: 'bad' }), 'alpha', false)).toEqual({
      ok: false, fields: { value: 'too small' }, form: 'beta: bad',
    });
  });

  it('puts a refused unset on the form line, even with an entry for the key', () => {
    expect(hubSettingRefusal(invalid({ alpha: 'too small' }), 'alpha', true)).toEqual({ ok: false, fields: {}, form: 'refused' });
  });

  it('puts anything else on the form line, falling back to Save failed.', () => {
    expect(hubSettingRefusal(invalid({ beta: 'bad' }), 'alpha', false)).toEqual({ ok: false, fields: {}, form: 'refused' });
    expect(hubSettingRefusal({ type: 'urn:blizzard:problem:setting-unknown', detail: 'unknown key', errors: {} }, 'alpha', false)).toEqual({
      ok: false, fields: {}, form: 'unknown key',
    });
    expect(hubSettingRefusal(new Error('network'), 'alpha', false)).toEqual({ ok: false, fields: {}, form: 'Save failed.' });
  });
});

import { parseSettingText, settingValueText } from './setting-value.domain';

describe('setting value', () => {
  it('shows a string as itself and any other value as JSON', () => {
    expect(settingValueText('a b')).toBe('a b');
    expect(settingValueText(60)).toBe('60');
    expect(settingValueText(null)).toBe('null');
    expect(settingValueText([1, 2])).toBe('[1,2]');
  });

  it('reads an edit by the current value: a string passes through, any other set value is JSON', () => {
    expect(parseSettingText('a', '{x')).toEqual({ value: '{x' });
    expect(parseSettingText('a', '75')).toEqual({ value: '75' });
    expect(parseSettingText(60, '75')).toEqual({ value: 75 });
    expect(parseSettingText(60, '{x')).toEqual({ error: 'Enter a valid JSON value.' });
  });

  it('reads an edit of an unset key as JSON when it parses and as plain text when it does not', () => {
    expect(parseSettingText(null, '12.5')).toEqual({ value: 12.5 });
    expect(parseSettingText(null, 'true')).toEqual({ value: true });
    expect(parseSettingText(null, '"quoted"')).toEqual({ value: 'quoted' });
    expect(parseSettingText(null, 'seven_day')).toEqual({ value: 'seven_day' });
    expect(parseSettingText(null, '~/.claude')).toEqual({ value: '~/.claude' });
  });
});

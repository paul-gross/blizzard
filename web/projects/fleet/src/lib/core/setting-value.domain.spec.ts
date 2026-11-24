import { parseSettingText, settingValueText } from './setting-value.domain';

describe('setting value', () => {
  it('shows a string as itself and any other value as JSON', () => {
    expect(settingValueText('a b')).toBe('a b');
    expect(settingValueText(60)).toBe('60');
    expect(settingValueText(null)).toBe('null');
    expect(settingValueText([1, 2])).toBe('[1,2]');
  });

  it('reads an edit by the current value: a string passes through, any other is JSON', () => {
    expect(parseSettingText('a', '{x')).toEqual({ value: '{x' });
    expect(parseSettingText(60, '75')).toEqual({ value: 75 });
    expect(parseSettingText(null, '12.5')).toEqual({ value: 12.5 });
    expect(parseSettingText(60, '{x')).toEqual({ error: 'Enter a valid JSON value.' });
  });
});

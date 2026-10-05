import { describe, expect, it } from 'vitest';

import { fieldDiffRows, formatDiffValue } from './config-field-diff.model';

describe('formatDiffValue', () => {
  it('renders unset as a dash, strings as they are, anything else as JSON', () => {
    expect(formatDiffValue(null)).toBe('—');
    expect(formatDiffValue(undefined)).toBe('—');
    expect(formatDiffValue('main')).toBe('main');
    expect(formatDiffValue(true)).toBe('true');
    expect(formatDiffValue(3)).toBe('3');
    expect(formatDiffValue(['a'])).toBe('["a"]');
  });
});

describe('fieldDiffRows', () => {
  it('maps each field change', () => {
    expect(fieldDiffRows([{ field: 'base_branch', old: 'master', new: 'main' }, { field: 'web_base', new: 'x' }])).toEqual([
      { field: 'base_branch', old: 'master', new: 'main' },
      { field: 'web_base', old: '—', new: 'x' },
    ]);
  });
});

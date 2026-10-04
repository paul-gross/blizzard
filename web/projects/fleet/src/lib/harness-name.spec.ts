import { harnessName } from './harness-name';

describe('harnessName', () => {
  it('reads an id with underscores as spaced words', () => {
    expect(harnessName('claude_code')).toBe('claude code');
  });

  it('leaves an id without underscores unchanged', () => {
    expect(harnessName('opencode')).toBe('opencode');
  });

  it('derives every multi-word id the same way, with no per-harness alias', () => {
    expect(harnessName('some_future_harness')).toBe('some future harness');
    expect(harnessName('claude')).toBe('claude');
  });
});

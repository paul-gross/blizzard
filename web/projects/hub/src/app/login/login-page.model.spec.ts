import { describe, expect, it } from 'vitest';

import { providersState } from './login-page.model';

describe('providersState', () => {
  it('is loading while the read is pending', () => {
    expect(providersState(true, false, [])).toBe('loading');
  });

  it('is error once the read fails', () => {
    expect(providersState(false, true, [])).toBe('error');
  });

  it('is empty with no providers', () => {
    expect(providersState(false, false, [])).toBe('empty');
  });

  it('is ready with at least one provider', () => {
    expect(providersState(false, false, [{ name: 'github' }])).toBe('ready');
  });
});

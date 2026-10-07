import { lifecycleTone } from './lifecycle-tone';

describe('lifecycleTone', () => {
  it('reads an enabled record cyan (spawning) and a retired one red (stale)', () => {
    expect(lifecycleTone(false)).toBe('spawning');
    expect(lifecycleTone(true)).toBe('stale');
  });
});

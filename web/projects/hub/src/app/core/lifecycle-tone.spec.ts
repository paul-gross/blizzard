import { graphLifecycleTone, lifecycleTone } from './lifecycle-tone';

describe('lifecycleTone', () => {
  it('reads an enabled record cyan (spawning) and a retired one red (stale)', () => {
    expect(lifecycleTone(false)).toBe('spawning');
    expect(lifecycleTone(true)).toBe('stale');
  });
});

describe('graphLifecycleTone', () => {
  it('reads effective cyan, retired red, and superseded dim', () => {
    expect(graphLifecycleTone('effective')).toBe('spawning');
    expect(graphLifecycleTone('retired')).toBe('stale');
    expect(graphLifecycleTone('superseded')).toBe('idle');
  });

  it('reads a lifecycle this build does not know as idle', () => {
    expect(graphLifecycleTone('archived' as never)).toBe('idle');
  });
});

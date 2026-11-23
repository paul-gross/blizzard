import { graphLifecycleTone } from './graph-lifecycle-tone';

describe('graphLifecycleTone', () => {
  it('reads effective cyan, retired red, and superseded dim', () => {
    expect(graphLifecycleTone('effective')).toBe('spawning');
    expect(graphLifecycleTone('retired')).toBe('stale');
    expect(graphLifecycleTone('superseded')).toBe('idle');
  });

  it('reads a lifecycle this build does not know as idle', () => {
    expect(graphLifecycleTone({ kind: 'unknown', raw: 'archived' })).toBe('idle');
  });
});

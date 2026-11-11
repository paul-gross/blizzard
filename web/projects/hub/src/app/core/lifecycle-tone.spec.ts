import {
  branchMarkerTone,
  builtInTone,
  graphLifecycleTone,
  lifecycleTone,
} from './lifecycle-tone';

describe('lifecycleTone', () => {
  it('reads an enabled record cyan (spawning) and a retired one red (stale)', () => {
    expect(lifecycleTone(false)).toBe('spawning');
    expect(lifecycleTone(true)).toBe('stale');
  });
});

describe('builtInTone', () => {
  it('reads the built-in marker grey (idle), never the enabled cyan', () => {
    expect(builtInTone).toBe('idle');
    expect(builtInTone).not.toBe(lifecycleTone(false));
  });
});

describe('branchMarkerTone', () => {
  it('reads an override amber (waiting) and an inherited branch the built-in grey', () => {
    expect(branchMarkerTone('override')).toBe('waiting');
    expect(branchMarkerTone('inherited')).toBe(builtInTone);
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

import { describe, expect, it } from 'vitest';

import { pendingLocalPause } from './app-pause-control.model';

describe('pendingLocalPause', () => {
  it('is null while no flip is pending', () => {
    expect(pendingLocalPause([], 'r1')).toBeNull();
  });

  it('ignores a flip pending for another runner', () => {
    expect(pendingLocalPause([{ runnerId: 'r2', paused: true }], 'r1')).toBeNull();
  });

  it("reads this runner's pending flip, either way", () => {
    expect(pendingLocalPause([{ runnerId: 'r2', paused: false }, { runnerId: 'r1', paused: true }], 'r1')).toBe(true);
    expect(pendingLocalPause([{ runnerId: 'r1', paused: false }], 'r1')).toBe(false);
  });
});

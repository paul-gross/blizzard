import { describe, expect, it } from 'vitest';

import { runnerFlagsKey } from './query-keys';

describe('runner query key identity', () => {
  it('runnerFlagsKey is the host-rooted flags literal', () => {
    expect(runnerFlagsKey).toEqual(['host', 'flags']);
  });
});

import { describe, expect, it } from 'vitest';
import { pauseStateView } from 'fleet/testing';

import { fromPauseStateView } from './pause-state.translate';

describe('fromPauseStateView', () => {
  it('carries both brakes and the local reason', () => {
    expect(fromPauseStateView(pauseStateView({ local: true, effective: true, local_reason: 'usage limit' }))).toEqual({
      local: true,
      hub: false,
      local_reason: 'usage limit',
    });
  });

  it('reads an absent pause as null', () => {
    expect(fromPauseStateView(null)).toBeNull();
    expect(fromPauseStateView(undefined)).toBeNull();
  });
});

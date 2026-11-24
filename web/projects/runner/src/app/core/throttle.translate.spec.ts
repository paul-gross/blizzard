import { describe, expect, it } from 'vitest';

import { fromThrottleStatusView } from './throttle.translate';

describe('fromThrottleStatusView', () => {
  it('carries the declining levers, floor and lever readings as reported', () => {
    const throttle = fromThrottleStatusView({
      state: 'declining',
      declining: ['floor', 'pace'],
      floor: {
        subscription: 'anthropic',
        admits: false,
        clear: null,
        hold: { kind: 'usage limit', harness_id: 'claude_code', window: '5h', expected_reset: 'r', awaits_sample: true, grace_opens_at: null, lease_id: null },
      },
      levers: [{ name: 'pace', setting: '7d', reading: '86%', admits: false, until: 'u' }],
      on_decline: 'hold',
      on_usage_limit: 'release',
    });
    expect(throttle?.declining).toEqual(['floor', 'pace']);
    expect(throttle?.floor.admits).toBe(false);
    expect(throttle?.floor.hold?.awaits_sample).toBe(true);
    expect(throttle?.levers).toEqual([{ name: 'pace', setting: '7d', reading: '86%', admits: false, until: 'u' }]);
  });

  it('reads an absent throttle as null', () => {
    expect(fromThrottleStatusView(null)).toBeNull();
  });
});

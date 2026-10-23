import type { runnerApi } from 'fleet';

import { throttleLines, throttleSummary } from './throttle.model';

const throttle: runnerApi.ThrottleStatusView = {
  state: 'declining', declining: ['floor'],
  floor: { subscription: 'anthropic', admits: false, clear: null, hold: {
    kind: 'usage limit', harness_id: 'claude_code', window: '5h', expected_reset: '2026-10-22T14:00:00Z',
    awaits_sample: true, grace_opens_at: '2026-10-22T15:00:00Z',
  } },
  levers: [{ name: 'pace', setting: '7d, lead 5 points', reading: '86% spent', admits: false, until: '2026-10-22T16:00:00Z' }],
  on_decline: 'hold', on_usage_limit: 'release',
};

describe('throttle words', () => {
  it('names the declining floor and its reset, without treating it as a brake', () => {
    expect(throttleSummary(throttle)).toContain('declining — floor, resets 2026-10-22T14:00:00Z');
    expect(throttleLines(throttle)[0].text).toContain('awaiting fresh sample, opens 2026-10-22T15:00:00Z');
    expect(throttleLines(throttle)[1].text).toContain('7d, lead 5 points · 86% spent · no · until');
    expect(throttleLines(throttle)[2].text).toBe('hold / release');
  });

  it('names a declining lever and when it expects to admit', () => {
    const pacing = { ...throttle, declining: ['pace'] } satisfies runnerApi.ThrottleStatusView;
    expect(throttleSummary(pacing)).toBe('declining — pace, until 2026-10-22T16:00:00Z');
  });

  it('names an unshared floor and a hand clear', () => {
    const open = { ...throttle, state: 'admitting', declining: [], floor: {
      subscription: null, admits: true, hold: null, clear: { by: 'paul', cleared_at: '2026-10-22T09:14:00Z' },
    } } satisfies runnerApi.ThrottleStatusView;
    expect(throttleSummary(open)).toBe('admitting');
    expect(throttleLines(open)[0].text).toContain('this runner: open — cleared by paul');
  });
});

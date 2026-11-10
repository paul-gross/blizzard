import type { runnerApi } from 'fleet';

import { throttleLines, throttleSummary } from './throttle.model';

const reset = new Date(2026, 9, 22, 14, 0, 0).toISOString();
const grace = new Date(2026, 9, 22, 15, 0, 0).toISOString();
const until = new Date(2026, 9, 22, 16, 0, 0).toISOString();
const cleared = new Date(2026, 9, 22, 9, 14, 0).toISOString();

const throttle: runnerApi.ThrottleStatusView = {
  state: 'declining', declining: ['floor'],
  floor: { subscription: 'anthropic', admits: false, clear: null, hold: {
    kind: 'usage limit', harness_id: 'claude_code', window: '5h', expected_reset: reset,
    awaits_sample: true, grace_opens_at: grace, lease_id: null,
  } },
  levers: [{ name: 'pace', setting: '7d, lead 5 points', reading: '86% spent', admits: false, until: until }],
  on_decline: 'hold', on_usage_limit: 'release',
};

describe('throttle words', () => {
  it('names the declining floor and its reset, without treating it as a brake', () => {
    expect(throttleSummary(throttle)).toContain('declining — floor, resets 2026/10/22 14:00:00');
    expect(throttleLines(throttle)[0].text).toContain('awaiting fresh sample, opens 2026/10/22 15:00:00');
    expect(throttleLines(throttle)[1].text).toContain('7d, lead 5 points · 86% spent · no · until 2026/10/22 16:00:00');
    expect(throttleLines(throttle)[2].text).toBe('hold / release');
  });

  it('names a declining lever and when it expects to admit', () => {
    const pacing = { ...throttle, declining: ['pace'] } satisfies runnerApi.ThrottleStatusView;
    expect(throttleSummary(pacing)).toBe('declining — pace, until 2026/10/22 16:00:00');
  });

  it('names an unshared floor and a hand clear', () => {
    const open = { ...throttle, state: 'admitting', declining: [], floor: {
      subscription: null, admits: true, hold: null, clear: { by: 'paul', cleared_at: cleared },
    } } satisfies runnerApi.ThrottleStatusView;
    expect(throttleSummary(open)).toBe('admitting');
    expect(throttleLines(open)[0].text).toContain('this runner: open — cleared by paul at 2026/10/22 09:14:00');
  });
  it('keeps unparseable instants visible in lines and summaries', () => {
    const invalid = { ...throttle, floor: { ...throttle.floor, hold: { ...throttle.floor.hold!, expected_reset: 'bad reset', grace_opens_at: 'bad grace' } }, levers: [{ name: 'pace', setting: '', reading: '', admits: false, until: 'bad until' }] } satisfies runnerApi.ThrottleStatusView;
    expect(throttleLines(invalid)[0].text).toContain('resets bad reset · awaiting fresh sample, opens bad grace');
    expect(throttleLines(invalid)[1].text).toContain('until bad until');
    expect(throttleSummary(invalid)).toContain('resets bad reset');
    expect(throttleSummary({ ...invalid, declining: ['pace'] })).toContain('until bad until');
    const open = { ...invalid, floor: { ...invalid.floor, admits: true, clear: { by: 'paul', cleared_at: 'bad clear' } } };
    expect(throttleLines(open)[0].text).toContain('at bad clear');
  });

});

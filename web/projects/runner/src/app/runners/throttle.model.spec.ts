import type { runnerApi } from 'fleet';

import { floorClearScope } from './throttle.model';

const runners = [
  { runner_id: 'rn_a', name: 'r-claude' },
  { runner_id: 'rn_b', name: 'r-chatgpt' },
  { runner_id: 'rn_c', name: 'r-test' },
] as runnerApi.HostRunnerView[];
const subscriptions = [{ slug: 'anthropic', spenders: ['rn_a', 'rn_b', 'rn_c'] }] as runnerApi.HostSubscriptionView[];

describe('floorClearScope', () => {
  it('names every spender of the subscription, not just the runner whose page is open', () => {
    expect(floorClearScope('anthropic', 'rn_a', runners, subscriptions)).toBe('r-claude, r-chatgpt, and r-test');
  });

  it('names a runner without a subscription alone', () => {
    expect(floorClearScope(null, 'rn_b', runners, subscriptions)).toBe('r-chatgpt');
  });

  it('withholds a subscription clear until its spenders are known', () => {
    expect(floorClearScope('anthropic', 'rn_a', runners, [])).toBeNull();
    expect(floorClearScope('anthropic', 'rn_a', runners, [{ ...subscriptions[0], spenders: [] }])).toBeNull();
  });

  it('uses the id of a spender absent from the host runner list rather than concealing them', () => {
    expect(floorClearScope('anthropic', 'rn_a', runners.slice(0, 1), subscriptions)).toBe('r-claude, rn_b, and rn_c');
  });
});

import type { runnerApi } from 'fleet/runner-shell';
import { runnerSubscription } from './host-subscription.translate';

describe('runnerSubscription', () => {
  it('copies windows and spenders while preserving sampling and renewal facts', () => {
    const row: runnerApi.HostSubscriptionView = {
      slug: 's', name: 'S', provider: 'p', condition: 'credential_lapsed',
      miss_reason: 'endpoint_unreachable', ok: false, sampled_at: 'attempt',
      windows_sampled_at: 'snapshot', spenders: ['r2', 'r1'],
      windows: [{ window: '5h', utilization_pct: 0, resets_at: 'reset', window_seconds: 18000 }],
      renewal_result: 'failed', renewal_failure_reason: 'timed_out', renewal_attempted_at: 'renewal',
    };
    const fact = runnerSubscription(row);
    expect(fact).toEqual(row);
    expect(fact.windows).not.toBe(row.windows);
    expect(fact.spenders).not.toBe(row.spenders);
  });

  it('preserves unrecognized vocabulary at the wire boundary and defaults missing collections', () => {
    const row = { slug: 's', name: 'S', provider: 'p', condition: 'new_condition', miss_reason: 'new_miss',
      renewal_result: 'deferred', renewal_failure_reason: 'new_failure' } as unknown as runnerApi.HostSubscriptionView;
    expect(runnerSubscription(row)).toMatchObject({ condition: 'new_condition', miss_reason: 'new_miss',
      renewal_result: 'deferred', renewal_failure_reason: 'new_failure', windows: [], spenders: [], sampled_at: null,
      windows_sampled_at: null, ok: null, renewal_attempted_at: null });
  });
});

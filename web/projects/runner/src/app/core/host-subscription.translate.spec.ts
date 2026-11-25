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
      revision: 4, settings: { name: 'S', credentials_path: '/c' }, state: 'retired',
    };
    const fact = runnerSubscription(row);
    expect(fact).toEqual(expect.objectContaining({ slug: row.slug, name: row.name, provider: row.provider,
      condition: row.condition, miss_reason: row.miss_reason, ok: row.ok, sampled_at: row.sampled_at,
      windows_sampled_at: row.windows_sampled_at, windows: row.windows, spenders: row.spenders,
      renewal_result: row.renewal_result, renewal_failure_reason: row.renewal_failure_reason, renewal_attempted_at: row.renewal_attempted_at,
      revision: 4, state: 'retired', settings: { name: 'S', credentials_path: '/c' } }));
    expect(fact.windows).not.toBe(row.windows);
    expect(fact.spenders).not.toBe(row.spenders);
  });

  it('preserves unrecognized vocabulary at the wire boundary and defaults missing collections', () => {
    const row = { slug: 's', name: 'S', provider: 'p', condition: 'new_condition', miss_reason: 'new_miss',
      renewal_result: 'deferred', renewal_failure_reason: 'new_failure' } as unknown as runnerApi.HostSubscriptionView;
    expect(runnerSubscription(row)).toMatchObject({ condition: { kind: 'unknown', raw: 'new_condition' }, miss_reason: { kind: 'unknown', raw: 'new_miss' },
      renewal_result: { kind: 'unknown', raw: 'deferred' }, renewal_failure_reason: { kind: 'unknown', raw: 'new_failure' }, windows: [], spenders: [], sampled_at: null,
      windows_sampled_at: null, ok: null, renewal_attempted_at: null, revision: null, state: 'active', settings: {} });
  });
});

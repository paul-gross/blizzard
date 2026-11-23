import type { HostRegistryView } from 'fleet/shell';
import { hostFact, subscriptionUsage } from './host.translate';

describe('hostFact', () => {
  it('keeps registry order, sample instants and spender ids while defaulting absent collections', () => {
    const wire = {
      host_id: 'h', host_name: 'host', connection: 'offline', online: false, enrolled: true,
      added_at: '2026-01-01T00:00:00Z', added_by: null, last_seen_at: null, registered_at: null,
      retired: true, url: null,
      workspaces: [{ workspace_id: 'b' }, { workspace_id: 'a', env_capacity: 0 }],
      subscription_usage: [{ slug: 's', name: 'S', sampled_at: '2026-01-01T01:00:00Z',
        missed_at: null, condition: null, miss_reason: null, windows: [], runners: ['r2', 'r1'] }],
    } as unknown as HostRegistryView;
    expect(hostFact(wire)).toMatchObject({
      retired: true, runner_ids: [], urls: [], subscriptions: [],
      workspaces: [{ workspace_id: 'b', env_capacity: null }, { workspace_id: 'a', env_capacity: 0 }],
      subscription_usage: [{ sampled_at: '2026-01-01T01:00:00Z', runners: ['r2', 'r1'] }],
    });
  });
  it('retains the sample time even with no windows', () => {
    expect(subscriptionUsage({ slug: 's', name: 'S', sampled_at: 't', missed_at: null,
      condition: null, miss_reason: null, windows: [] })).toMatchObject({ sampled_at: 't', windows: [] });
  });
  it('preserves unknown connection and sample labels', () => {
    const wire = { host_id: 'h', host_name: 'H', online: false, connection: 'reconnecting', enrolled: true,
      added_at: 't', retired: false, subscription_usage: [{ slug: 's', name: 'S', condition: 'new_condition', miss_reason: 'new_miss' }] } as unknown as HostRegistryView;
    expect(hostFact(wire)).toMatchObject({ connection: { kind: 'unknown', raw: 'reconnecting' }, subscription_usage: [{ condition: { kind: 'unknown', raw: 'new_condition' }, miss_reason: { kind: 'unknown', raw: 'new_miss' }, windows: [], runners: [], sampled_at: null }] });
  });
});

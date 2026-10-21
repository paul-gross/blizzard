import type { HostRegistryView, SubscriptionUsageView } from '../api/hub';
import { hostSubscriptionPaces, hostWorkspaceRows, hubSubscriptionPaces } from './hub-host.model';

const NOW = Date.parse('2026-07-16T12:00:00.000Z');

/** A fully-shaped host, defaulting to an online host reporting nothing. */
function host(over: Partial<HostRegistryView> = {}): HostRegistryView {
  return {
    host_id: 'hs_1',
    host_name: 'h1',
    added_at: '2026-07-16T10:00:00.000Z',
    connection: 'online',
    enrolled: true,
    online: true,
    added_by: null,
    last_seen_at: null,
    redirect_uris: [],
    registered_at: null,
    retired: false,
    retired_at: null,
    retired_by: null,
    runner_ids: [],
    subscription_usage: [],
    subscriptions: [],
    url: null,
    workspaces: [],
    ...over,
  };
}

describe('hubSubscriptionPaces', () => {
  it('folds a sampled usage to its bars, refreshed label, and freshness tier', () => {
    const usage: SubscriptionUsageView[] = [
      {
        slug: 'anthropic',
        name: 'Anthropic',
        sampled_at: '2026-07-16T11:55:00.000Z',
        condition: null,
        miss_reason: null,
        missed_at: null,
        windows: [{ window: '5h', utilization_pct: 40, resets_at: '2026-07-16T14:30:00.000Z', window_seconds: 18_000 }],
      },
    ];
    expect(hubSubscriptionPaces(usage, NOW)).toEqual([
      {
        slug: 'anthropic',
        name: 'Anthropic',
        paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 50 }],
        condition: null,
        sampledAt: '2026-07-16T11:55:00.000Z',
        refreshedLabel: 'refreshed 5m ago',
        refreshedTag: '[5m]',
        freshness: 'fresh',
        missReason: null,
      },
    ]);
  });

  it('leaves a never-sampled usage without a label or tier, carrying its miss reason', () => {
    const [pace] = hubSubscriptionPaces(
      [{ slug: 'openai', name: 'OpenAI', windows: [], miss_reason: 'endpoint_unreachable', condition: null, missed_at: null, sampled_at: null }],
      NOW,
    );
    expect(pace).toMatchObject({ sampledAt: null, refreshedLabel: null, refreshedTag: null, freshness: null, missReason: 'endpoint_unreachable' });
  });
});

describe('hostSubscriptionPaces', () => {
  it('lists each host subscription once, naming the runners that spend it', () => {
    const names: Record<string, string> = { rn_a: 'rA', rn_b: 'rB' };
    const paces = hostSubscriptionPaces(
      host({
        subscription_usage: [
          { slug: 'anthropic', name: 'Anthropic', windows: [], runners: ['rn_a', 'rn_b'], condition: null, miss_reason: null, missed_at: null, sampled_at: null },
          { slug: 'openai', name: 'OpenAI', windows: [], runners: [], condition: null, miss_reason: null, missed_at: null, sampled_at: null },
        ],
      }),
      NOW,
      (id) => names[id] ?? id,
    );
    expect(paces.map((p) => [p.slug, p.spenders])).toEqual([
      ['anthropic', ['rA', 'rB']],
      ['openai', []],
    ]);
  });

  it('folds a host reporting no usage to no rows', () => {
    expect(hostSubscriptionPaces(host(), NOW, (id) => id)).toEqual([]);
  });
});

describe('hostWorkspaceRows', () => {
  it('maps each reported workspace to its capacity and environments in use, in wire order', () => {
    const rows = hostWorkspaceRows(
      host({ workspaces: [{ workspace_id: 'ws-1', env_capacity: 3 }, { workspace_id: 'ws-2' }] }),
      new Map([['ws-1', 2]]),
    );
    expect(rows).toEqual([
      { workspaceId: 'ws-1', capacity: 3, inUse: 2, flag: null, environments: [] },
      { workspaceId: 'ws-2', capacity: null, inUse: 0, flag: null, environments: [] },
    ]);
  });
});

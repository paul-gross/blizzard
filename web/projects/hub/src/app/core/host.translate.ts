import type { HostRegistryView, SubscriptionUsageView, HostSubscriptionUsageView, HostFact, HostUsage, SubscriptionUsage } from 'fleet/shell';
import { hostConnection, sampleMiss } from 'fleet/shell';

export function subscriptionUsage(row: SubscriptionUsageView): SubscriptionUsage {
  return {
    slug: row.slug, name: row.name,
    windows: (row.windows ?? []).map(({ window, utilization_pct, resets_at, window_seconds }) =>
      ({ window, utilization_pct, resets_at, window_seconds })),
    sampled_at: row.sampled_at ?? null, missed_at: row.missed_at ?? null,
    condition: sampleMiss(row.condition),
    miss_reason: sampleMiss(row.miss_reason),
  };
}

export function hostUsage(row: HostSubscriptionUsageView): HostUsage {
  return { ...subscriptionUsage(row), runners: [...(row.runners ?? [])] };
}

export function hostFact(row: HostRegistryView): HostFact {
  return {
    host_id: row.host_id, host_name: row.host_name, online: row.online, connection: hostConnection(row.connection),
    last_seen_at: row.last_seen_at ?? null, url: row.url ?? null, urls: [...(row.urls ?? [])],
    enrolled: row.enrolled, registered_at: row.registered_at ?? null, added_at: row.added_at,
    added_by: row.added_by ?? null, retired: row.retired, runner_ids: [...(row.runner_ids ?? [])],
    workspaces: (row.workspaces ?? []).map(({ workspace_id, env_capacity }) => ({ workspace_id, env_capacity: env_capacity ?? null })),
    subscription_usage: (row.subscription_usage ?? []).map(hostUsage),
    subscriptions: (row.subscriptions ?? []).map(({ slug, name }) => ({ slug, name })),
  };
}

import type { HostRegistryView, RunnerBindingView, RunnerRegistryView, SubscriptionUsageView } from '../../api/hub';
import { fixtureInstant } from './clock';

/** One registered runner, online and unpaused with no host, pool or subscriptions, at whatever id the caller names. */
export function runnerRegistryView(
  overrides: Pick<RunnerRegistryView, 'runner_id'> & Partial<RunnerRegistryView>,
): RunnerRegistryView {
  return {
    added_at: fixtureInstant(-3600),
    added_by: null,
    agents: null,
    bindings: null,
    capabilities: [],
    connection: 'online',
    declining: [],
    env_capacity: null,
    gates: [],
    host_id: null,
    hub_paused: false,
    last_seen_at: fixtureInstant(0),
    locally_paused: false,
    locally_paused_by: null,
    locally_paused_reason: null,
    online: true,
    projects: [],
    registered_at: fixtureInstant(-3600),
    retired: false,
    retired_at: null,
    retired_by: null,
    runner_name: overrides.runner_id,
    subscription: null,
    subscription_unrostered: false,
    subscriptions: [],
    unenforceable_gates: [],
    version: null,
    version_refusal: null,
    version_standing: null,
    workspace_id: 'ws_a',
    ...overrides,
  };
}

/** One registered host, online and enrolled with no workspaces, runners or subscriptions, at whatever id the caller names. */
export function hostRegistryView(overrides: Pick<HostRegistryView, 'host_id'> & Partial<HostRegistryView>): HostRegistryView {
  return {
    added_at: fixtureInstant(-3600),
    added_by: null,
    connection: 'online',
    enrolled: true,
    host_name: overrides.host_id,
    last_seen_at: fixtureInstant(0),
    online: true,
    redirect_uris: [],
    registered_at: fixtureInstant(-3600),
    retired: false,
    retired_at: null,
    retired_by: null,
    runner_ids: [],
    subscription_usage: [],
    subscriptions: [],
    url: null,
    workspaces: [],
    ...overrides,
  };
}

/** One project-workspace binding of a runner, reported by its workspace. */
export function runnerBindingView(
  overrides: Pick<RunnerBindingView, 'project' | 'workspace_id'> & Partial<RunnerBindingView>,
): RunnerBindingView {
  return { agents: 1, workspace_unreported: false, ...overrides };
}

/** One subscription's usage sample on a runner, with no windows and no miss, at whatever slug the caller names. */
export function subscriptionUsageView(
  overrides: Pick<SubscriptionUsageView, 'slug'> & Partial<SubscriptionUsageView>,
): SubscriptionUsageView {
  return {
    condition: null,
    miss_reason: null,
    missed_at: null,
    name: overrides.slug,
    sampled_at: fixtureInstant(0),
    windows: [],
    ...overrides,
  };
}

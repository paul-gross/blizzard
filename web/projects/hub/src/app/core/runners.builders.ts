import type { HubRunner, RunnerCapabilityFact } from './runners.domain';

/** One registered runner, online and unpaused with no host, pool or subscriptions, at whatever id the caller names. */
export function hubRunner(overrides: Pick<HubRunner, 'runner_id'> & Partial<HubRunner>): HubRunner {
  return {
    runner_name: overrides.runner_id,
    host_id: null,
    workspace_id: 'ws_a',
    connection: 'online',
    online: true,
    last_seen_at: '2026-01-01T00:00:00Z',
    added_at: '2025-12-31T23:00:00Z',
    added_by: null,
    registered_at: '2025-12-31T23:00:00Z',
    retired: false,
    retired_at: null,
    retired_by: null,
    hub_paused: false,
    locally_paused: false,
    locally_paused_by: null,
    locally_paused_reason: null,
    agents: null,
    env_capacity: null,
    bindings: null,
    projects: [],
    capabilities: [],
    gates: [],
    unenforceable_gates: [],
    declining: [],
    subscription: null,
    subscription_unrostered: false,
    subscriptions: [],
    version: null,
    version_standing: null,
    version_refusal: null,
    ...overrides,
  };
}

/** One available harness binding, with no tiers or models, at whatever harness the caller names. */
export function runnerCapability(overrides: Pick<RunnerCapabilityFact, 'harness_id'> & Partial<RunnerCapabilityFact>): RunnerCapabilityFact {
  return { available: true, default: false, version: null, tiers: [], supported_models: [], ...overrides };
}

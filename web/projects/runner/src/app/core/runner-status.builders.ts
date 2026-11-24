import { pauseState } from './pause-state.builders';
import type { DashboardStatus, HubConnectivity, IdentityVerdict, RunnerCapacities, RunnerStatus } from './runner-status.domain';

/** A runner's link to its hub: reachable, nothing buffered, never contacted. */
export function hubConnectivity(overrides: Partial<HubConnectivity> = {}): HubConnectivity {
  return {
    endpoint: 'http://127.0.0.1:4522',
    reachable: true,
    last_contact_at: null,
    buffer_depth: 0,
    hub_version: null,
    window: null,
    ...overrides,
  };
}

/** A runner's slot accounting: nothing bound, nothing in use. */
export function runnerCapacities(overrides: Partial<RunnerCapacities> = {}): RunnerCapacities {
  return { agents: 0, used: 0, free: 0, bindings: [], ...overrides };
}

/** An identity verdict: matched. */
export function identityVerdict(overrides: Partial<IdentityVerdict> = {}): IdentityVerdict {
  return {
    state: 'matched',
    reason: null,
    reset_command: 'blizzard runner reset-identity',
    token_runner_id: null,
    store_runner_id: null,
    checked_at: null,
    ...overrides,
  };
}

/** A runner's own status: `rn_1` on `workspace-local`, unpaused, idle, reaching its hub. */
export function runnerStatus(overrides: Partial<RunnerStatus> = {}): RunnerStatus {
  return {
    runner_id: 'rn_1',
    runner_name: null,
    tenant_id: null,
    tenant_name: null,
    workspace_id: 'workspace-local',
    last_tick_at: null,
    identity: null,
    hub: hubConnectivity(),
    pause: pauseState(),
    capacities: runnerCapacities(),
    routing_conflicts: [],
    unmatched_bindings: [],
    served_projects: null,
    gates: [],
    unenforceable_gates: [],
    throttle: null,
    ...overrides,
  };
}

/** A runner's dashboard status: {@link runnerStatus}, no fleet counts, no harnesses. */
export function dashboardStatus(overrides: Partial<DashboardStatus> = {}): DashboardStatus {
  return { runner: runnerStatus(), fleet_summary: null, harness_health: [], ...overrides };
}

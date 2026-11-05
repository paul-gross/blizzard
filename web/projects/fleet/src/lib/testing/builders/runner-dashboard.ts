import type {
  CapacitiesView,
  DashboardView,
  HostRunnerView,
  HubConnectivityView,
  PauseStateView,
  RunnerStatusView,
} from '../../api/runner';

/** A runner's brake state: nothing holding it. */
export function pauseStateView(overrides: Partial<PauseStateView> = {}): PauseStateView {
  return { local: false, hub: false, effective: false, local_reason: null, ...overrides };
}

/** A runner's slot accounting: nothing bound, nothing in use. */
export function capacitiesView(overrides: Partial<CapacitiesView> = {}): CapacitiesView {
  return { agents: 0, bindings: [], free: 0, used: 0, ...overrides };
}

/** A runner's link to its hub: reachable, nothing buffered, never contacted. */
export function hubConnectivityView(overrides: Partial<HubConnectivityView> = {}): HubConnectivityView {
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

/** A runner's own status block: `rn_1` on `workspace-local`, unpaused, idle, reaching its hub. */
export function runnerStatusView(overrides: Partial<RunnerStatusView> = {}): RunnerStatusView {
  return {
    runner_id: 'rn_1',
    runner_name: null,
    tenant_id: null,
    tenant_name: null,
    workspace_id: 'workspace-local',
    capacities: capacitiesView(),
    gates: [],
    hub: hubConnectivityView(),
    identity: null,
    last_tick_at: null,
    pause: pauseStateView(),
    routing_conflicts: [],
    served_projects: null,
    throttle: null,
    unenforceable_gates: null,
    unmatched_bindings: [],
    ...overrides,
  };
}

/** A runner's whole dashboard read: {@link runnerStatusView} with every list empty and no fleet summary. */
export function runnerDashboardView(overrides: Partial<DashboardView> = {}): DashboardView {
  return {
    asks: { items: [] },
    escalations: { items: [] },
    facts: { items: [] },
    fleet_summary: null,
    harness_health: { items: [] },
    runner: runnerStatusView(),
    takeovers: { items: [] },
    ...overrides,
  };
}

/** One runner as the host lists it: `rn_1`, serving, unbound and idle. */
export function hostRunnerView(overrides: Partial<HostRunnerView> = {}): HostRunnerView {
  return {
    runner_id: 'rn_1',
    name: 'r1',
    state: 'serving',
    served: true,
    halted_reasons: [],
    pause: pauseStateView(),
    throttle: null,
    bindings: [],
    agents_in_use: 0,
    max_agents: 0,
    ...overrides,
  };
}


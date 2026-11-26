import type { UnknownHostValue } from 'fleet/core';
import type { UnenforceableGate } from 'fleet/host';

import type { PauseState } from './pause-state.domain';
import type { Throttle } from './throttle.domain';

export type KnownIdentityState =
  | 'unchecked'
  | 'matched'
  | 'mismatched'
  | 'refused'
  | 'not_held'
  | 'tenant_differs'
  | 'version_refused';
/** The hub's verdict on a runner's token; `UnknownHostValue` carries one this build cannot name. */
export type IdentityState = KnownIdentityState | UnknownHostValue;

/** The hub's verdict on whose token a runner holds, with the remedy for a refusal. */
export interface IdentityVerdict {
  readonly state: IdentityState;
  readonly reason: string | null;
  readonly reset_command: string;
  readonly token_runner_id: string | null;
  readonly store_runner_id: string | null;
  readonly checked_at: string | null;
}

/** A runner's link to its hub; `reachable` is the runner's own judgment of it. */
export interface HubConnectivity {
  readonly endpoint: string;
  readonly reachable: boolean;
  readonly last_contact_at: string | null;
  readonly buffer_depth: number;
  readonly hub_version: string | null;
  /** The runner versions the hub admits, `null` until a registration names them. */
  readonly window: { readonly floor: string; readonly ceiling: string } | null;
}

/** One binding's slot accounting. */
export interface BindingCapacity {
  readonly project: string;
  readonly project_id: string | null;
  readonly workspace_id: string;
  readonly agents: number;
  readonly used: number;
  readonly free: number;
}

/** A runner's slot accounting, in total and per binding. */
export interface RunnerCapacities {
  readonly agents: number;
  readonly used: number;
  readonly free: number;
  readonly bindings: readonly BindingCapacity[];
}

/** Binding entries that all name one project, so the runner claims nothing for it. */
export interface RoutingConflict {
  readonly project_id: string;
  readonly entries: readonly string[];
}

/** An environment holding a chunk under no binding the runner declares. */
export interface UnmatchedBinding {
  readonly chunk_id: string;
  readonly environment_id: string;
  readonly held_since: string;
  readonly workdir: string;
}

/** One project entry the runner declares, as its hub resolved it. */
export interface ServedProjectStatus {
  readonly entry: string;
  readonly project_id: string | null;
  readonly slug: string | null;
  readonly name: string | null;
  readonly color: string | null;
  readonly resolved: boolean;
  readonly answered: boolean;
}

/** A runner's own status: who it is, its hub link and identity verdict, its brakes, slots and throttle. */
export interface RunnerStatus {
  readonly runner_id: string | null;
  readonly runner_name: string | null;
  readonly tenant_id: string | null;
  readonly tenant_name: string | null;
  readonly workspace_id: string;
  readonly last_tick_at: string | null;
  readonly identity: IdentityVerdict | null;
  readonly hub: HubConnectivity;
  readonly pause: PauseState;
  readonly capacities: RunnerCapacities;
  readonly routing_conflicts: readonly RoutingConflict[];
  readonly unmatched_bindings: readonly UnmatchedBinding[];
  /** `null` while the runner's hub has resolved none. */
  readonly served_projects: readonly ServedProjectStatus[] | null;
  readonly gates: readonly string[];
  readonly unenforceable_gates: readonly UnenforceableGate[];
  readonly throttle: Throttle | null;
}

/** The hub's chunk counts the runner passes through. */
export interface FleetSummary {
  readonly needs: number;
  readonly ready: number;
  readonly running: number;
  readonly waiting: number;
}

/** One harness the runner binds, as its own health check found it; `available` is that check's verdict. */
export interface HarnessHealth {
  readonly harness_id: string;
  readonly version: string | null;
  readonly available: boolean;
  readonly admitted_range: string | null;
  readonly cause: string | null;
  readonly degradations: readonly string[];
}

/** A runner's dashboard read as its status sections: its own status (`null` for a body carrying none), the
 * hub's fleet counts (`null` through a hub outage), and its harnesses' health. */
export interface DashboardStatus {
  readonly runner: RunnerStatus | null;
  readonly fleet_summary: FleetSummary | null;
  readonly harness_health: readonly HarnessHealth[];
}

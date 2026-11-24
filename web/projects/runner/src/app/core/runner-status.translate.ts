import type { UnenforceableGate, UnknownHostValue } from 'fleet';
import type { runnerApi } from 'fleet/runner-shell';
import { openLookup } from 'fleet/shell';

import { fromPauseStateView } from './pause-state.translate';
import type { DashboardStatus, HarnessHealth, IdentityState, IdentityVerdict, RunnerStatus } from './runner-status.domain';
import { fromThrottleStatusView } from './throttle.translate';

const unknown = (raw: string): UnknownHostValue => ({ kind: 'unknown', raw });

const identityStates = openLookup<runnerApi.IdentityState, IdentityState>(
  {
    unchecked: 'unchecked',
    matched: 'matched',
    mismatched: 'mismatched',
    refused: 'refused',
    not_held: 'not_held',
    tenant_differs: 'tenant_differs',
    version_refused: 'version_refused',
  },
  unknown,
);

const gateReasons = openLookup<runnerApi.UnenforceableGateReason, UnenforceableGate['reason']>(
  { 'hub-executed': 'hub-executed', 'unknown-node': 'unknown-node' },
  unknown,
);

export function fromUnenforceableGateView(wire: runnerApi.UnenforceableGateView): UnenforceableGate {
  return { node: wire.node, reason: gateReasons(wire.reason) };
}

export function fromIdentityVerdictView(wire: runnerApi.IdentityVerdictView | null | undefined): IdentityVerdict | null {
  if (!wire) return null;
  return {
    state: identityStates(wire.state),
    reason: wire.reason ?? null,
    reset_command: wire.reset_command,
    token_runner_id: wire.token_runner_id ?? null,
    store_runner_id: wire.store_runner_id ?? null,
    checked_at: wire.checked_at ?? null,
  };
}

/** A runner's own status, or `null` for a body that carries no runner or hub link. */
export function fromRunnerStatusView(wire: runnerApi.RunnerStatusView | null | undefined): RunnerStatus | null {
  if (!wire?.hub) return null;
  const { hub, capacities } = wire;
  return {
    runner_id: wire.runner_id,
    runner_name: wire.runner_name,
    tenant_id: wire.tenant_id,
    tenant_name: wire.tenant_name,
    workspace_id: wire.workspace_id,
    last_tick_at: wire.last_tick_at ?? null,
    identity: fromIdentityVerdictView(wire.identity),
    hub: {
      endpoint: hub.endpoint,
      reachable: hub.reachable,
      last_contact_at: hub.last_contact_at ?? null,
      buffer_depth: hub.buffer_depth,
      hub_version: hub.hub_version ?? null,
      window: hub.window ? { floor: hub.window.floor, ceiling: hub.window.ceiling } : null,
    },
    pause: fromPauseStateView(wire.pause) ?? { local: false, hub: false, effective: false, local_reason: null },
    capacities: {
      agents: capacities?.agents ?? 0,
      used: capacities?.used ?? 0,
      free: capacities?.free ?? 0,
      bindings: (capacities?.bindings ?? []).map(({ project, project_id, workspace_id, agents, used, free }) => ({
        project,
        project_id,
        workspace_id,
        agents,
        used,
        free,
      })),
    },
    routing_conflicts: (wire.routing_conflicts ?? []).map(({ project_id, entries }) => ({ project_id, entries: [...entries] })),
    unmatched_bindings: (wire.unmatched_bindings ?? []).map(({ chunk_id, environment_id, held_since, workdir }) => ({
      chunk_id,
      environment_id,
      held_since,
      workdir,
    })),
    served_projects:
      wire.served_projects?.map(({ entry, project_id, slug, name, color, resolved, answered }) => ({
        entry,
        project_id,
        slug,
        name: name ?? null,
        color: color ?? null,
        resolved,
        answered,
      })) ?? null,
    gates: [...(wire.gates ?? [])],
    unenforceable_gates: (wire.unenforceable_gates ?? []).map(fromUnenforceableGateView),
    throttle: fromThrottleStatusView(wire.throttle),
  };
}

function fromHarnessHealthView(wire: runnerApi.HarnessHealthView): HarnessHealth {
  return {
    harness_id: wire.harness_id,
    version: wire.version ?? null,
    available: wire.available,
    admitted_range: wire.admitted_range ?? null,
    cause: wire.cause ?? null,
    degradations: [...(wire.degradations ?? [])],
  };
}

/** A runner's dashboard read as its status sections. */
export function fromDashboardView(wire: runnerApi.DashboardView): DashboardStatus {
  const fleet = wire.fleet_summary;
  return {
    runner: fromRunnerStatusView(wire.runner),
    fleet_summary: fleet ? { needs: fleet.needs, ready: fleet.ready, running: fleet.running, waiting: fleet.waiting } : null,
    harness_health: (wire.harness_health?.items ?? []).map(fromHarnessHealthView),
  };
}

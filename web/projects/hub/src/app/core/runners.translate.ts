import type {
  DeclaredLever,
  RunnerCapability,
  RunnerDecliningView,
  RunnerRegistryView,
  RunnerVersionStanding,
  ServedProjectView,
  UnenforceableGate,
  UnenforceableGateView,
  hubApi,
  UnknownHostValue,
  VersionRefusalReason,
} from 'fleet';
import { hostConnection, openLookup } from 'fleet/shell';
import type {
  DeclaredLeverKind,
  HubRunner,
  ProjectRunner,
  RunnerCapabilityFact,
  RunnerDeclining,
  ServedProject,
  VersionRefusal,
  VersionStanding,
} from './runners.domain';
import { subscriptionUsage } from './host.translate';

const unknown = (raw: string): UnknownHostValue => ({ kind: 'unknown', raw });

const standings = openLookup<RunnerVersionStanding, VersionStanding>(
  { in_window: 'in_window', unversioned: 'unversioned', refused: 'refused' },
  unknown,
);
const refusals = openLookup<VersionRefusalReason, VersionRefusal>(
  { 'too-old': 'too-old', 'too-new': 'too-new', unversioned: 'unversioned' },
  unknown,
);
const levers = openLookup<DeclaredLever, DeclaredLeverKind>(
  { floor: 'floor', pace: 'pace', utilization: 'utilization', budget: 'budget', schedule: 'schedule' },
  unknown,
);
const gateReasons = openLookup<hubApi.UnenforceableGateReason, UnenforceableGate['reason']>(
  { 'hub-executed': 'hub-executed', 'unknown-node': 'unknown-node' },
  unknown,
);

export function toProjectRunner(runner: RunnerRegistryView): ProjectRunner {
  return {
    runner_id: runner.runner_id,
    runner_name: runner.runner_name,
    connection: runner.connection,
    online: runner.online,
    projects: (runner.projects ?? []).map(({ project_id, claimable }) => ({
      project_id,
      claimable,
    })),
  };
}

export function toUnenforceableGate(wire: UnenforceableGateView): UnenforceableGate {
  return { node: wire.node, reason: gateReasons(wire.reason) };
}

export function toServedProject(wire: ServedProjectView): ServedProject {
  return {
    entry: wire.entry,
    project_id: wire.project_id ?? null,
    slug: wire.slug ?? null,
    name: wire.name ?? null,
    color: wire.color ?? null,
    claimable: wire.claimable,
  };
}

export function toRunnerCapability(wire: RunnerCapability): RunnerCapabilityFact {
  return {
    harness_id: wire.harness_id,
    available: wire.available ?? false,
    default: wire.default ?? false,
    version: wire.version ?? null,
    tiers: [...(wire.tiers ?? [])],
    supported_models: [...(wire.supported_models ?? [])],
  };
}

export function toRunnerDeclining(wire: RunnerDecliningView): RunnerDeclining {
  return {
    lever: wire.lever,
    lever_kind: levers(wire.lever_kind),
    detail: wire.detail,
    subscription: wire.subscription ?? null,
    until: wire.until ?? null,
  };
}

export function toHubRunner(wire: RunnerRegistryView): HubRunner {
  return {
    runner_id: wire.runner_id,
    runner_name: wire.runner_name,
    host_id: wire.host_id ?? null,
    workspace_id: wire.workspace_id ?? null,
    connection: hostConnection(wire.connection),
    online: wire.online,
    last_seen_at: wire.last_seen_at ?? null,
    added_at: wire.added_at,
    added_by: wire.added_by ?? null,
    registered_at: wire.registered_at ?? null,
    retired: wire.retired,
    retired_at: wire.retired_at ?? null,
    retired_by: wire.retired_by ?? null,
    hub_paused: wire.hub_paused ?? false,
    locally_paused: wire.locally_paused ?? false,
    locally_paused_by: wire.locally_paused_by ?? null,
    locally_paused_reason: wire.locally_paused_reason ?? null,
    agents: wire.agents ?? null,
    env_capacity: wire.env_capacity ?? null,
    bindings: wire.bindings == null
      ? null
      : wire.bindings.map(({ project, workspace_id, agents, workspace_unreported }) => ({
          project, workspace_id, agents, workspace_unreported: workspace_unreported ?? false,
        })),
    projects: (wire.projects ?? []).map(toServedProject),
    capabilities: (wire.capabilities ?? []).map(toRunnerCapability),
    gates: [...(wire.gates ?? [])],
    unenforceable_gates: (wire.unenforceable_gates ?? []).map(toUnenforceableGate),
    declining: (wire.declining ?? []).map(toRunnerDeclining),
    subscription: wire.subscription ?? null,
    subscription_unrostered: wire.subscription_unrostered ?? false,
    subscriptions: (wire.subscriptions ?? []).map(subscriptionUsage),
    version: wire.version ?? null,
    version_standing: wire.version_standing == null ? null : standings(wire.version_standing),
    version_refusal: wire.version_refusal == null ? null : refusals(wire.version_refusal),
  };
}

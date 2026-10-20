import { type BeaconTone, compactRef, harnessName, type runnerApi, type Tone } from 'fleet';

import type { MachineChunkRow } from '../core/chunk-rows.model';
import type { PanelProject } from '../core/panel-reads.model';
import { slugForEntry } from '../core/panel-reads.model';

/** One binding of a runner: the project it serves in one workspace, and its agents in use of those it declares. */
export interface HostRunnerBinding {
  readonly key: string;
  readonly projectName: string;
  /** The project's colour for its swatch, `null` where the hub names none. */
  readonly color: string | null;
  readonly workspaceId: string;
  readonly used: number;
  readonly agents: number;
}

/** One harness the runner binds, as its own health check found it. */
export interface HostRunnerHarness {
  readonly harnessId: string;
  readonly name: string;
  readonly version: string | null;
  readonly available: boolean;
  /** The versions the runner admits, for the badge's tooltip. */
  readonly admitted: string | null;
  /** Why it is unavailable, `null` while it is available. */
  readonly cause: string | null;
  /** The limits the harness declares of itself — standing, not faults — for a hover beside it. */
  readonly degradations: readonly string[];
}

/** A badge on a runner's State line. */
export interface HostRunnerBadge {
  readonly label: string;
  readonly tone: Tone;
  readonly title: string | null;
}

/** The runner's link to its hub. */
export interface HostRunnerHub {
  readonly endpoint: string;
  readonly reachable: boolean;
  readonly lastContactAt: string | null;
  readonly buffered: number;
  /** The hub's own version, `null` until a registration has answered. */
  readonly hubVersion: string | null;
  /** "admits 0.2 – 0.3" — the runner versions the hub's window admits, `null` until it names one. */
  readonly window: string | null;
  /** Why the hub will not take the runner's work, `null` while its token checks out or goes unchecked. */
  readonly refusal: HostRunnerBadge | null;
}

/** A chunk the runner holds: still running, parked, or waiting on a human. */
export interface HostRunnerChunk {
  readonly chunkId: string;
  readonly ref: string;
  readonly projectName: string | null;
  readonly color: string | null;
  readonly node: string;
  readonly status: { readonly label: string; readonly tone: Tone };
}

/** One runner on the host page, every value resolved for display. */
export interface HostRunnerRow {
  readonly key: string;
  readonly runnerId: string | null;
  readonly name: string;
  readonly ref: string | null;
  readonly tone: BeaconTone;
  readonly halted: boolean;
  /** "Claude Code · 24 agents · 7 busy" — its harnesses, then its agents. */
  readonly summary: string | null;
  readonly lastTickAt: string | null;
  readonly bindings: readonly HostRunnerBinding[];
  /** The subscriptions the runner spends, by name. */
  readonly spends: readonly string[];
  readonly harnesses: readonly HostRunnerHarness[];
  readonly gates: readonly string[];
  readonly hub: HostRunnerHub | null;
  /** What the runner cannot act on — entries naming one project twice, environments held under no binding. */
  readonly problems: readonly string[];
  readonly state: readonly HostRunnerBadge[];
  readonly held: readonly HostRunnerChunk[];
  /** Whether its held chunks are still loading — no count, and no WORKING or IDLE claim, until they land. */
  readonly heldPending: boolean;
}

/** Why the hub refuses the runner's work, from its identity verdict; `null` while it checks out or goes unchecked. */
function refusal(identity: runnerApi.IdentityVerdictView | null | undefined): HostRunnerBadge | null {
  switch (identity?.state) {
    case 'mismatched':
      return { label: 'REFUSING', tone: 'needs', title: identity.reset_command };
    case 'refused':
      return { label: 'REFUSED', tone: 'needs', title: [identity.reason, identity.reset_command].filter(Boolean).join(' — ') || null };
    case 'not_held':
      return { label: 'NOT HELD', tone: 'needs', title: identity.reason ?? null };
    case 'tenant_differs':
      return { label: 'TENANT DIFFERS', tone: 'needs', title: identity.reason ?? null };
    case 'version_refused':
      return { label: 'VERSION REFUSED', tone: 'needs', title: identity.reason ?? null };
    default:
      return null;
  }
}

/** The runner's bindings, the host's declaration joined to the runner's own slot accounting and its projects' names. */
function bindings(
  runner: runnerApi.HostRunnerView,
  capacities: runnerApi.CapacitiesView | undefined,
  projects: readonly PanelProject[],
): readonly HostRunnerBinding[] {
  return (runner.bindings ?? []).map((binding) => {
    const used = capacities?.bindings?.find(
      (capacity) => capacity.project === binding.project && capacity.workspace_id === binding.workspace_id,
    )?.used;
    const slug = slugForEntry(projects, binding.project);
    const project = projects.find((candidate) => candidate.slug === slug);
    return {
      key: `${binding.project}@${binding.workspace_id}`,
      projectName: project?.name ?? binding.project,
      color: project?.color ?? null,
      workspaceId: binding.workspace_id,
      used: used ?? 0,
      agents: binding.agents,
    };
  });
}

function harnesses(health: readonly runnerApi.HarnessHealthView[]): readonly HostRunnerHarness[] {
  return health.map((harness) => ({
    harnessId: harness.harness_id,
    name: harnessName(harness.harness_id),
    version: harness.version ?? null,
    available: harness.available,
    admitted: harness.admitted_range ?? null,
    cause: harness.cause ?? null,
    degradations: harness.degradations ?? [],
  }));
}

function problems(status: runnerApi.RunnerStatusView | undefined): readonly string[] {
  return [
    ...(status?.routing_conflicts ?? []).map(
      (conflict) => `${conflict.entries.join(', ')} all name ${conflict.project_id} — claiming nothing until one is removed`,
    ),
    ...(status?.unmatched_bindings ?? []).map(
      (held) => `${held.environment_id} holds ${compactRef(held.chunk_id)} under no binding this runner declares`,
    ),
  ];
}

function stateBadges(
  runner: runnerApi.HostRunnerView,
  status: runnerApi.RunnerStatusView | undefined,
  held: number | null,
): readonly HostRunnerBadge[] {
  if (runner.state !== 'serving') {
    return [{ label: 'HALTED', tone: 'needs', title: (runner.halted_reasons ?? []).join('; ') || null }];
  }
  const pause = status?.pause ?? runner.pause;
  const badges: HostRunnerBadge[] = [];
  if (status && !status.hub.reachable) badges.push({ label: 'HUB UNREACHABLE', tone: 'stale', title: status.hub.endpoint });
  if (pause?.local) {
    badges.push({ label: 'PAUSED HERE', tone: 'idle', title: pause.local_reason ?? 'Paused on this runner — no new claims' });
  }
  if (pause?.hub) badges.push({ label: 'HUB PAUSED', tone: 'waiting', title: 'Paused by the hub — no new claims' });
  if (badges.length === 0 && held !== null) {
    badges.push({ label: held > 0 ? 'WORKING' : 'IDLE', tone: held > 0 ? 'running' : 'idle', title: null });
  }
  return badges;
}

/** Red for a halted runner or one cut off from its hub, amber while either brake holds it, else green. */
function tone(runner: runnerApi.HostRunnerView, status: runnerApi.RunnerStatusView | undefined): BeaconTone {
  if (runner.state !== 'serving' || (status && !status.hub.reachable) || refusal(status?.identity)) return 'red';
  const pause = status?.pause ?? runner.pause;
  return pause?.local || pause?.hub ? 'amber' : 'green';
}

/** "Claude Code · 24 agents · 7 busy". */
function summary(runner: runnerApi.HostRunnerView, health: readonly HostRunnerHarness[]): string | null {
  const parts = health.map((harness) => harness.name);
  const agents = runner.max_agents ?? 0;
  if (agents > 0) parts.push(`${agents} ${agents === 1 ? 'agent' : 'agents'}`, `${runner.agents_in_use ?? 0} busy`);
  return parts.length === 0 ? null : parts.join(' · ');
}

/** The reads one {@link HostRunnerRow} folds, each keyed by runner id. */
export interface HostRunnerReads {
  readonly dashboards: ReadonlyMap<string, runnerApi.DashboardView>;
  readonly projects: ReadonlyMap<string, readonly PanelProject[]>;
  readonly subscriptions: readonly runnerApi.HostSubscriptionView[];
  /** Every chunk across the runners, as the board folds them. */
  readonly chunks: readonly MachineChunkRow[];
  /** Whether the chunks are still loading, so no runner claims it holds none. */
  readonly chunksPending: boolean;
}

/** One {@link HostRunnerRow} per runner the host serves or holds halted, in wire order. */
export function hostRunnerRows(runners: readonly runnerApi.HostRunnerView[], reads: HostRunnerReads): readonly HostRunnerRow[] {
  return runners.map((runner, index) => {
    const id = runner.runner_id;
    const dashboard = id === null ? undefined : reads.dashboards.get(id);
    const status = dashboard?.runner;
    const health = harnesses(dashboard?.harness_health?.items ?? []);
    const held = reads.chunks
      .filter((chunk) => chunk.lease.owner.runnerId === id && chunk.lane !== 'done')
      .map((chunk) => ({
        chunkId: chunk.lease.chunk_id,
        ref: compactRef(chunk.lease.chunk_id),
        projectName: chunk.projectName,
        color: chunk.projectColor,
        node: chunk.lease.node_name,
        status: chunk.status,
      }));
    return {
      key: id ?? `${runner.name}#${index}`,
      runnerId: id,
      name: runner.name,
      ref: id === null ? null : compactRef(id),
      tone: tone(runner, status),
      halted: runner.state !== 'serving',
      summary: summary(runner, health),
      lastTickAt: status?.last_tick_at ?? null,
      bindings: bindings(runner, status?.capacities, id === null ? [] : (reads.projects.get(id) ?? [])),
      spends: id === null ? [] : reads.subscriptions.filter((sub) => sub.spenders?.includes(id)).map((sub) => sub.name),
      harnesses: health,
      gates: status?.gates ?? [],
      hub: status
        ? {
            endpoint: status.hub.endpoint,
            reachable: status.hub.reachable,
            lastContactAt: status.hub.last_contact_at ?? null,
            buffered: status.hub.buffer_depth,
            hubVersion: status.hub.hub_version ?? null,
            window: status.hub.window ? `admits ${status.hub.window.floor} – ${status.hub.window.ceiling}` : null,
            refusal: refusal(status.identity),
          }
        : null,
      problems: problems(status),
      state: stateBadges(runner, status, reads.chunksPending ? null : held.length),
      held,
      heldPending: reads.chunksPending,
    };
  });
}

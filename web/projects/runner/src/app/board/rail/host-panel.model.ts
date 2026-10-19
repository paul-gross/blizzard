import {
  type BeaconTone,
  compactRef,
  type runnerApi,
  type SlotBarSlot,
} from 'fleet';

/** One binding of a runner: the project it serves in one workspace, and its agents in use of those it declares. */
export interface HostPanelBinding {
  readonly key: string;
  /** The project entry, with the workspace beside it only when the runner binds more than one workspace. */
  readonly label: string;
  readonly used: number;
  readonly agents: number;
}

/** One runner on the host panel: its light, its bindings' agents in use, and its brakes. */
export interface HostPanelRunner {
  readonly key: string;
  readonly runnerId: string | null;
  readonly name: string;
  /** Red for a halted runner, yellow while either brake holds it, else green. */
  readonly tone: BeaconTone;
  readonly halted: boolean;
  readonly haltedReasons: readonly string[];
  readonly agentsInUse: number;
  readonly maxAgents: number;
  /** One line per binding, in declaration order — empty for a runner that declares none, which shows its one total. */
  readonly bindings: readonly HostPanelBinding[];
  /** The runner's own brake, worded for its glyph's tooltip; `null` while it is off. */
  readonly localPause: string | null;
  readonly hubPaused: boolean;
}

/** One environment as a block: free, or held by a chunk of a project — named in the block's tooltip. */
export interface HostPanelEnv {
  readonly environmentId: string;
  readonly held: boolean;
  /** The holding chunk's short ref, `null` for a free one. */
  readonly chunkRef: string | null;
  readonly chunkId: string | null;
  /** The holding chunk's project, as the panel's filters name it; `null` where none is told. */
  readonly project: string | null;
  /** The holding runner's display name, `null` where the host names none. */
  readonly holder: string | null;
  /** Held by a runner other than the one the Runner filter names — the block dims. */
  readonly elsewhere: boolean;
}

/** One workspace's environments as a row of blocks. */
export interface HostPanelPool {
  readonly workspaceId: string;
  /** Its declared ceiling, `null` for a workspace the host reports but does not declare. */
  readonly capacity: number | null;
  readonly inUse: number;
  readonly envs: readonly HostPanelEnv[];
  /** One block per environment in its own order, lit where held — which slot is busy, not only how many. */
  readonly slots: readonly SlotBarSlot[];
}

/** A block's tooltip: the environment, then — held — its chunk, project and runner. */
function envTitle(env: HostPanelEnv): string {
  if (!env.held) return `${env.environmentId} · free`;
  return [env.environmentId, env.chunkRef, env.project, env.holder]
    .filter((part) => part !== null)
    .join(' · ');
}

/** A runner's brake in operator words for its glyph, or `null` while its own brake is off. */
function localPauseText(
  pause: runnerApi.PauseStateView | null | undefined,
): string | null {
  if (!pause?.local) return null;
  return pause.local_reason
    ? `Paused here: ${pause.local_reason}`
    : 'Paused here — no new claims';
}

/** A runner's binding lines: each binding the host declares for it, its agents in use read off the runner's own
 * slot accounting (`capacities`, `undefined` until that read lands, when every binding reads 0 in use). */
function bindingLines(
  bindings: readonly runnerApi.HostRunnerBindingView[],
  capacities: runnerApi.CapacitiesView | undefined,
): readonly HostPanelBinding[] {
  const manyWorkspaces =
    new Set(bindings.map((binding) => binding.workspace_id)).size > 1;
  return bindings.map((binding) => {
    const used = capacities?.bindings?.find(
      (capacity) =>
        capacity.project === binding.project &&
        capacity.workspace_id === binding.workspace_id,
    )?.used;
    return {
      key: `${binding.project}@${binding.workspace_id}`,
      label: manyWorkspaces
        ? `${binding.project} · ${binding.workspace_id}`
        : binding.project,
      used: used ?? 0,
      agents: binding.agents,
    };
  });
}

/** One {@link HostPanelRunner} per runner the host serves or holds halted, in wire order, each serving runner's bindings
 * filled from its slot accounting in `capacities` (by runner id). */
export function hostPanelRunners(
  runners: readonly runnerApi.HostRunnerView[],
  capacities: ReadonlyMap<string, runnerApi.CapacitiesView>,
): readonly HostPanelRunner[] {
  return runners.map((runner, index) => {
    const halted = runner.state !== 'serving';
    const localPause = halted ? null : localPauseText(runner.pause);
    const hubPaused = !halted && (runner.pause?.hub ?? false);
    return {
      key: runner.runner_id ?? `${runner.name}#${index}`,
      runnerId: runner.runner_id,
      name: runner.name,
      tone: halted
        ? 'red'
        : localPause !== null || hubPaused
          ? 'amber'
          : 'green',
      halted,
      haltedReasons: halted ? (runner.halted_reasons ?? []) : [],
      agentsInUse: runner.agents_in_use ?? 0,
      maxAgents: runner.max_agents ?? 0,
      bindings: halted
        ? []
        : bindingLines(
            runner.bindings ?? [],
            capacities.get(runner.runner_id ?? ''),
          ),
      localPause,
      hubPaused,
    };
  });
}

/**
 * One {@link HostPanelPool} per declared workspace, in declaration order, then any workspace the
 * host reports environments under without declaring it. `focus` is the runner id set the Runner
 * filter leaves — `null` for All — and dims every block another runner holds.
 */
export function hostPanelPools(
  workspaces: readonly runnerApi.HostWorkspaceView[],
  groups: readonly runnerApi.HostWorkspaceEnvironmentsView[],
  runnerName: (runnerId: string) => string,
  projectName: (entry: string) => string,
  focus: ReadonlySet<string> | null,
): readonly HostPanelPool[] {
  const byId = new Map(
    groups.map((group) => [group.workspace_id, group.items ?? []]),
  );
  const declared = new Set(workspaces.map((ws) => ws.workspace_id));
  const pool = (
    workspaceId: string,
    capacity: number | null,
  ): HostPanelPool => {
    const envs = [...(byId.get(workspaceId) ?? [])]
      .sort((a, b) =>
        a.environment_id.localeCompare(b.environment_id, undefined, {
          numeric: true,
        }),
      )
      .map((env): HostPanelEnv => {
        const held = env.held ?? env.chunk_id != null;
        const runnerId = env.runner_id ?? null;
        return {
          environmentId: env.environment_id,
          held,
          chunkRef: env.chunk_id == null ? null : compactRef(env.chunk_id),
          chunkId: env.chunk_id ?? null,
          project:
            env.project == null || env.project === ''
              ? null
              : projectName(env.project),
          holder: runnerId === null ? null : runnerName(runnerId),
          elsewhere:
            held &&
            focus !== null &&
            (runnerId === null || !focus.has(runnerId)),
        };
      });
    const slots = envs.map((env) => ({
      on: env.held,
      title: envTitle(env),
      dim: env.elsewhere,
    }));
    return {
      workspaceId,
      capacity,
      inUse: envs.filter((env) => env.held).length,
      envs,
      slots,
    };
  };
  return [
    ...workspaces.map((ws) => pool(ws.workspace_id, ws.max_environments)),
    ...groups
      .filter((group) => !declared.has(group.workspace_id))
      .map((group) => pool(group.workspace_id, null)),
  ];
}

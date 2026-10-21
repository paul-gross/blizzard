import { compactRef, type runnerApi, type SlotBarSlot } from 'fleet';

import { type PanelProject, slugForEntry } from './panel-reads.model';

/** A runner id's display name off the host's runner list — the id itself for one the list
 * does not (yet) name. */
export function runnerNameResolver(runners: readonly runnerApi.HostRunnerView[]): (runnerId: string) => string {
  const names = new Map(runners.flatMap((runner) => (runner.runner_id === null ? [] : [[runner.runner_id, runner.name] as const])));
  return (runnerId) => names.get(runnerId) ?? runnerId;
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
    groups.map((group) => [group.workspace_id, group.items]),
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
        const held = env.held;
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

/** The runner ids a block holds without dimming: `null` — none dim — while the Runner filter is All
 * (`runnerFilter` `null`), else the ids of the runners `items` leaves, each read through `idOf`. */
export function runnerFocus<T>(
  runnerFilter: string | null,
  items: readonly T[],
  idOf: (item: T) => string | null,
): ReadonlySet<string> | null {
  if (runnerFilter === null) return null;
  return new Set(items.flatMap((item) => idOf(item) ?? []));
}

/** The host reads {@link hostPoolsFor} folds. */
export interface HostPoolReads {
  readonly workspaces: readonly runnerApi.HostWorkspaceView[];
  readonly groups: readonly runnerApi.HostWorkspaceEnvironmentsView[];
  readonly runners: readonly runnerApi.HostRunnerView[];
  readonly projects: readonly PanelProject[];
}

/** {@link hostPanelPools} over the host's reads, each holder named off its runner list and each
 * project entry resolved to the slug the panel's filters name it by. */
export function hostPoolsFor(reads: HostPoolReads, focus: ReadonlySet<string> | null): readonly HostPanelPool[] {
  return hostPanelPools(
    reads.workspaces,
    reads.groups,
    runnerNameResolver(reads.runners),
    (entry) => slugForEntry(reads.projects, entry) ?? entry,
    focus,
  );
}

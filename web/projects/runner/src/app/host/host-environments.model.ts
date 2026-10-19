import type { HostPanelEnv, HostPanelPool } from '../core/host-pools.model';
import type { PanelProject } from '../core/panel-reads.model';

/** One environment as a line on the host page: free, or held — its chunk, project and runner named. */
export interface HostEnvLine extends HostPanelEnv {
  /** The holding chunk's project by name, `null` for a free one or one no project is told for. */
  readonly projectName: string | null;
  /** That project's colour for its swatch, `null` where the hub names none. */
  readonly color: string | null;
}

/** One workspace's environments as lines, under its id and its in-use count. */
export interface HostEnvGroup {
  readonly workspaceId: string;
  readonly capacity: number | null;
  readonly inUse: number;
  readonly envs: readonly HostEnvLine[];
}

/** `pools` as line groups, each held environment's project resolved by slug to its name and colour. */
export function hostEnvGroups(pools: readonly HostPanelPool[], projects: readonly PanelProject[]): readonly HostEnvGroup[] {
  return pools.map((pool) => ({
    workspaceId: pool.workspaceId,
    capacity: pool.capacity,
    inUse: pool.inUse,
    envs: pool.envs.map((env) => {
      const project = env.project === null ? undefined : projects.find((candidate) => candidate.slug === env.project);
      return { ...env, projectName: project?.name ?? env.project, color: project?.color ?? null };
    }),
  }));
}

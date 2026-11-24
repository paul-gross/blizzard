/** One environment in a host workspace — whether a runner holds it, which, and for which chunk of which project. */
export interface HostEnvironment {
  readonly environment_id: string;
  readonly held: boolean;
  readonly chunk_id: string | null;
  /** The holding chunk's project entry as the host names it, `null` where it names none. */
  readonly project: string | null;
  readonly runner_id: string | null;
}

/** A host workspace's environments. */
export interface WorkspaceEnvironments {
  readonly workspace_id: string;
  readonly items: readonly HostEnvironment[];
}

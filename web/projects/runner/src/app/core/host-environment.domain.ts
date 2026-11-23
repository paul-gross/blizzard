/** One environment in a host workspace — whether a runner holds it, and which. */
export interface HostEnvironment {
  readonly held: boolean;
  readonly runner_id: string | null;
}

/** A host workspace's environments. */
export interface WorkspaceEnvironments {
  readonly items: readonly HostEnvironment[];
}

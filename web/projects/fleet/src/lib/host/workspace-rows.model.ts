/** One environment row under a {@link WorkspaceRow} — every value is already resolved
 * for display (including {@link heldFor}'s clock-driven text), so whatever renders it
 * injects nothing. `holder` names who holds it, `null` where the daemon does not say. */
export interface EnvironmentRow {
  readonly environmentId: string;
  readonly isHeld: boolean;
  readonly chunkRef: string;
  readonly heldFor: string;
  readonly holder: string | null;
}

/** One workspace a host reports, with its environments. A `null` {@link workspaceId} is a pool
 * its daemon reports without a workspace: it has no heading of its own.
 * `capacity` is `null` when the workspace reports none; `flag` is an operator-facing
 * mark, `null` for none. */
export interface WorkspaceRow {
  readonly workspaceId: string | null;
  readonly capacity: number | null;
  readonly inUse: number;
  readonly flag: string | null;
  readonly environments: readonly EnvironmentRow[];
}

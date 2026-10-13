import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitBeacon } from '../kit/kit-beacon';

/** One environment row under a {@link WorkspaceRow} — every value is already resolved
 * for display (including {@link heldFor}'s clock-driven text), so the view itself
 * injects nothing. `holder` names who holds it, `null` where the daemon does not say. */
export interface EnvironmentRow {
  readonly environmentId: string;
  readonly isHeld: boolean;
  readonly chunkRef: string;
  readonly heldFor: string;
  readonly holder: string | null;
}

/** One workspace row for {@link WorkspaceTable}. A `null` {@link workspaceId} is a pool
 * its daemon reports without a workspace: its environments render without a heading.
 * `capacity` is `null` when the workspace reports none; `flag` is an operator-facing
 * mark, `null` for none. */
export interface WorkspaceRow {
  readonly workspaceId: string | null;
  readonly capacity: number | null;
  readonly inUse: number;
  readonly flag: string | null;
  readonly environments: readonly EnvironmentRow[];
}

/**
 * The shared workspace-and-environment table: one heading per workspace — its id, its
 * environments in use of its capacity, and its flag — over its environment rows.
 *
 * Presentational only (`bzh:frontend-container-presentational`): each daemon's container
 * folds its own read into the rows and keeps the ticking clock the rows are aged against.
 */
@Component({
  selector: 'fleet-workspace-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBeacon],
  templateUrl: './workspace-table.html',
  styleUrl: './workspace-table.css',
})
export class WorkspaceTable {
  readonly workspaces = input<readonly WorkspaceRow[]>([]);
}

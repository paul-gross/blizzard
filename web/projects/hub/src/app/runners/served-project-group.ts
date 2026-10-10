import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitBadge } from 'fleet';

/**
 * The runner registry's served-projects render — "Serves:" followed by one badge per project the runner serves. A
 * runner that declares no project gets an UNASSIGNED marker instead, so an idle runner reads as unassigned rather than
 * as starved; each entry of its declaration that names no project of the tenant gets an UNRESOLVED marker naming the
 * entry as the runner wrote it.
 *
 * Presentational only.
 */
@Component({
  selector: 'app-served-project-group',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
  templateUrl: './served-project-group.html',
  styleUrl: './served-project-group.css',
})
export class ServedProjectGroup {
  /** The display name of each project the runner serves, in its declaration's order. */
  readonly served = input.required<readonly string[]>();

  /** Whether the runner declares no project at all. */
  readonly unassigned = input.required<boolean>();

  /** The entries of the runner's declaration that name no project of the tenant. */
  readonly unresolved = input.required<readonly string[]>();
}

import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitBadge } from 'fleet';
import type { BindingLine } from './host-rows.model';

/**
 * The runner registry's served-projects render — "Serves:" followed by one badge per project the runner serves. A
 * runner that declares no project gets an UNASSIGNED marker instead, so an idle runner reads as unassigned rather than
 * as starved; each entry of its declaration that names no project of the tenant gets an UNRESOLVED marker naming the
 * entry as the runner wrote it.
 *
 * A runner under a host renders in bindings mode instead: one line per binding — its project, its workspace, and its
 * agents in use of those it declares — with a binding in a workspace its host does not report marked UNREPORTED in the
 * UNRESOLVED marker's tone, and the runner's one subscription named, flagged UNROSTERED the same way when its host's
 * roster does not carry it.
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

  /** The runner's binding lines — set for a runner under a host, switching to bindings mode; `null` for a host-less
   * runner. */
  readonly bindings = input<readonly BindingLine[] | null>(null);

  /** The display name of the runner's one subscription in bindings mode, `null` for none. */
  readonly subscription = input<string | null>(null);

  /** Whether the runner's subscription is missing from its host's roster. */
  readonly subscriptionUnrostered = input(false);
}

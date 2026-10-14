import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { asyncState, injectNowSignal, KitAsyncState, runnerWorkspaceRows, WorkspaceTable, type WorkspaceRow } from 'fleet';

import { injectHostEnvironmentsQuery, injectHostWorkspacesQuery, injectRunnerNames } from './host.query';

/**
 * The host's environments **container**: one heading per workspace the host declares,
 * its environments in use of its capacity, over one row per environment — a held one
 * naming the runner holding it and its chunk's project. Owns the two host reads, the
 * resolved async-state triad, and the ticking clock each row's held-for text is derived
 * from; the shared presentational {@link WorkspaceTable} owns the row template
 * (`bzh:frontend-container-presentational`). The empty state renders only when the host
 * declares no workspace and reports no environment.
 */
@Component({
  selector: 'app-host-environments',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, WorkspaceTable],
  templateUrl: './host-environments.html',
  styleUrl: './host-environments.css',
})
export class HostEnvironments {
  protected readonly environmentsQuery = injectHostEnvironmentsQuery();
  private readonly workspacesQuery = injectHostWorkspacesQuery();
  private readonly runnerNames = injectRunnerNames();

  /** Ticks once a second so each row's `heldFor` advances between polls instead of
   * sitting frozen at whatever age the last read carried. */
  private readonly now = injectNowSignal(1000);

  protected readonly workspaces = computed<readonly WorkspaceRow[]>(() =>
    runnerWorkspaceRows(
      this.workspacesQuery.data() ?? [],
      this.environmentsQuery.data() ?? [],
      this.now(),
      this.runnerNames(),
    ),
  );

  /** The async triad's resolved state — the environments read's loading/error take
   * precedence, then a host with nothing to list, else the rows render. */
  protected readonly triadState = computed(() => asyncState(this.environmentsQuery, this.workspaces().length === 0));
}

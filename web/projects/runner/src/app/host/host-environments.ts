import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { asyncState, injectNowSignal, KitAsyncState, runnerWorkspaceRows, WorkspaceTable, type WorkspaceRow } from 'fleet';

import { PanelFilters } from '../core/panel-filters';
import { injectPanelProjects, injectPanelRunners } from '../core/panel-reads';
import { slugForEntry } from '../core/panel-reads.model';
import { injectHostEnvironmentsQuery, injectHostWorkspacesQuery, injectRunnerNames } from './host.query';

/**
 * The host's environments **container**: one heading per workspace the host declares,
 * its environments in use of its capacity, over one row per environment — a held one
 * naming the runner holding it and its chunk's project. Under a filter, only the
 * environments the named runner holds, or that hold the named project's chunks, are listed.
 * Owns the two host reads, the
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
  private readonly filters = inject(PanelFilters);
  private readonly runners = injectPanelRunners();
  private readonly projects = injectPanelProjects();

  /** The host's environments the filters leave. */
  private readonly environments = computed(() => {
    const runnerName = this.filters.runner();
    const project = this.filters.project();
    if (runnerName === null && project === null) return this.environmentsQuery.data() ?? [];
    const runnerIds = new Set(this.runners.scoped().map((owner) => owner.runnerId));
    const projects = this.projects();
    return (this.environmentsQuery.data() ?? []).map((workspace) => ({
      ...workspace,
      items: (workspace.items ?? []).filter(
        (env) =>
          (runnerName === null || (env.runner_id != null && runnerIds.has(env.runner_id))) &&
          (project === null || (env.project != null && (slugForEntry(projects, env.project) ?? env.project) === project)),
      ),
    }));
  });

  /** Ticks once a second so each row's `heldFor` advances between polls instead of
   * sitting frozen at whatever age the last read carried. */
  private readonly now = injectNowSignal();

  protected readonly workspaces = computed<readonly WorkspaceRow[]>(() =>
    runnerWorkspaceRows(
      this.workspacesQuery.data() ?? [],
      this.environments(),
      this.now(),
      this.runnerNames(),
    ),
  );

  /** The async triad's resolved state — the environments read's loading/error take
   * precedence, then a host with nothing to list, else the rows render. */
  protected readonly triadState = computed(() => asyncState(this.environmentsQuery, this.workspaces().length === 0));
}

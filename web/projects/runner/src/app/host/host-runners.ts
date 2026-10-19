import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { asyncState, KitAsyncState } from 'fleet';

import { PanelFilters } from '../core/panel-filters';
import { injectRunnerProjects } from '../core/panel-reads';
import { servesProject } from '../core/panel-reads.model';
import { injectHostRunnersQuery } from './host.query';
import { hostRunnerRows, type HostRunnerRow } from './host-runners.model';
import { HostRunnersView } from './host-runners-view';

/**
 * The host's runners **container** — owns the host's runner read and its async-state
 * triad, and folds the read into rows ({@link hostRunnerRows}) — the runners the filters
 * leave: the one the Runner filter names, those serving the Project filter's project; the presentational
 * {@link HostRunnersView} owns the row template (`bzh:frontend-container-presentational`).
 */
@Component({
  selector: 'app-host-runners',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HostRunnersView, KitAsyncState],
  templateUrl: './host-runners.html',
  styleUrl: './host-runners.css',
})
export class HostRunners {
  protected readonly query = injectHostRunnersQuery();
  private readonly filters = inject(PanelFilters);
  private readonly projects = injectRunnerProjects();

  protected readonly rows = computed<readonly HostRunnerRow[]>(() => {
    const runnerName = this.filters.runner();
    const project = this.filters.project();
    const byRunner = this.projects();
    return hostRunnerRows(
      (this.query.data() ?? []).filter(
        (runner) =>
          (runnerName === null || runner.name === runnerName) &&
          (project === null || (runner.runner_id !== null && servesProject(byRunner.get(runner.runner_id) ?? [], project))),
      ),
    );
  });

  protected readonly triadState = computed(() => asyncState(this.query, this.rows().length === 0));
}

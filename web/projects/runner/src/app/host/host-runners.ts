import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { asyncState, KitAsyncState } from 'fleet';

import { injectHostRunnersQuery } from './host.query';
import { hostRunnerRows, type HostRunnerRow } from './host-runners.model';
import { HostRunnersView } from './host-runners-view';

/**
 * The host's runners **container** — owns the host's runner read and its async-state
 * triad, and folds the read into rows ({@link hostRunnerRows}); the presentational
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

  protected readonly rows = computed<readonly HostRunnerRow[]>(() => hostRunnerRows(this.query.data() ?? []));

  protected readonly triadState = computed(() => asyncState(this.query, this.rows().length === 0));
}

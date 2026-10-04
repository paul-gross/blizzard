import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { asyncState, injectNowSignal, KitAsyncState } from 'fleet';

import { type EnvRow, EnvListView } from './env-list-view';
import { envRows } from './env-list.model';
import { injectRunnerDashboardQuery } from '../core/status.query';

/**
 * The environments rail **container**: one row per environment in the
 * runner's configured pool — the wire (`GET /api/dashboard`) carries the full
 * pool, so the panel never invents pool facts of its own. Owns the query, the
 * resolved async-state triad, and the ticking clock {@link EnvRow.heldFor} is
 * derived from; the presentational {@link EnvListView} owns the row template
 * (`bzh:frontend-container-presentational`). The empty state renders only when the
 * pool itself is empty, not merely unheld.
 */
@Component({
  selector: 'app-env-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, EnvListView],
  templateUrl: './env-list.html',
  styleUrl: './env-list.css',
})
export class EnvList {
  protected readonly query = injectRunnerDashboardQuery();

  protected readonly envs = computed(() => this.query.data()?.environments?.items ?? []);

  /** The async triad's resolved state — loading/error take precedence, then
   * an empty pool (no environments configured at all), else the rows render. */
  protected readonly triadState = computed(() => asyncState(this.query, this.envs().length === 0));

  /** Ticks once a second so each row's `heldFor` advances between polls instead of
   * sitting frozen at whatever age the last read carried. */
  private readonly now = injectNowSignal(1000);

  protected readonly rows = computed<readonly EnvRow[]>(() => envRows(this.envs(), this.now()));
}

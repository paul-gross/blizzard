import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { asyncState, injectNowSignal, KitAsyncState, runnerWorkspaceRows, WorkspaceTable, type WorkspaceRow } from 'fleet';

import { injectRunnerDashboardQuery } from '../core/status.query';

/**
 * The environments rail **container**: one row per environment in the
 * runner's configured pool — the wire (`GET /api/dashboard`) carries the full
 * pool, so the panel never invents pool facts of its own. Owns the query, the
 * resolved async-state triad, and the ticking clock each row's held-for text
 * is derived from; the shared presentational {@link WorkspaceTable} owns the row template
 * (`bzh:frontend-container-presentational`). The empty state renders only when the
 * pool itself is empty, not merely unheld.
 */
@Component({
  selector: 'app-env-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, WorkspaceTable],
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

  protected readonly workspaces = computed<readonly WorkspaceRow[]>(() => runnerWorkspaceRows(this.envs(), this.now()));
}
